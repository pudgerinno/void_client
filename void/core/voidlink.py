"""Void Link — кооп через интернет. Порт передаётся вручную из лаунчера."""
import base64, hashlib, hmac, json, os, socket, struct, threading, time, zlib
LOG = None
def _log(m):
    if LOG: LOG.info("[voidlink] " + m)

try:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    HAVE_CRYPTO = True
except Exception:
    HAVE_CRYPTO = False

PROTO = 4

HDR = struct.Struct("!4sB")
COUNTER = 12
T_PUNCH, T_HELLO, T_ACK, T_DATA, T_BYE = 1, 2, 3, 4, 5
K_OPEN, K_DATA, K_CLOSE = 0, 1, 2
FRAME = struct.Struct("!HBH")
MAXP = 1050
WINDOW = 64
RTO = 0.15
CODE_TTL = 900


STUN_HOSTS = [
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
    ("stun2.l.google.com", 19302),
    ("stun.stunprotocol.org", 3478),
    ("stun.lanik.io", 3478),
    ("stun.sipgate.net", 3478),
    ("stun.ekiga.net", 3478),
]


def _stun(timeout=4.0):
    """Узнаёт внешний ip:port через STUN. Возвращает (ip, port)."""
    last_err = "нет ответа"
    for host, port in STUN_HOSTS:
        s = None
        try:
            tid = os.urandom(12)
            # Binding Request: тип 0x0001, длина 0, маг. печенье, транзакционный ID.
            # ВАЖНО: длина именно 0 — никаких атрибутов, иначе сервер отбросит.
            req = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(req, (host, port))
            data, _ = s.recvfrom(2048)
            if len(data) < 20 or data[4:8] != b"\x21\x12\xa4\x42":
                last_err = f"{host}: некорректный ответ"
                continue
            i = 20
            while i + 4 <= len(data):
                at, al = struct.unpack("!HH", data[i:i + 4])
                val = data[i + 4:i + 4 + al]
                if at in (0x0020, 0x0001) and len(val) >= 8:
                    xp = struct.unpack("!H", val[2:4])[0]
                    xi = struct.unpack("!I", val[4:8])[0]
                    if at == 0x0020:          # XOR-MAPPED-ADDRESS
                        xp ^= 0x2112
                        xi ^= 0x2112A442
                    ip = socket.inet_ntoa(struct.pack("!I", xi))
                    _log(f"stun: {host}:{port} -> {ip}:{xp}")
                    return ip, xp
                i += 4 + al + ((4 - al % 4) % 4)
            last_err = f"{host}: ответ без адреса"
        except socket.timeout:
            last_err = f"{host}:{port}: таймаут"
        except Exception as e:
            last_err = f"{host}:{port}: {security.redact(str(e)) if 'security' in globals() else e}"
        finally:
            if s:
                try: s.close()
                except OSError: pass
    raise RuntimeError(f"STUN не ответил ({last_err}). Либо нет интернета, либо заблокирован исходящий UDP.")

def _local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def make_code(info):
    return "VOID1:" + base64.urlsafe_b64encode(
        zlib.compress(json.dumps(info, separators=(",", ":")).encode())).decode().rstrip("=")


def read_code(code):
    code = (code or "").strip()
    if not code.startswith("VOID1:"):
        raise RuntimeError("Не похоже на код (должен начинаться с VOID1:)")
    b = code[6:]; b += "=" * (-len(b) % 4)
    info = json.loads(zlib.decompress(base64.urlsafe_b64decode(b)))
    if int(info.get("v", 0)) != PROTO:
        raise RuntimeError("Несовместимая версия кода — попроси новый")
    if time.time() > info.get("exp", 0):
        raise RuntimeError("Код истёк — попроси новый")
    return info


class Tunnel:
    def __init__(self, role, sid, bind_port=0, on_status=None, on_log=None):
        self.role = role; self.sid = sid
        self.on_status = on_status or (lambda *a, **k: None)
        self.log = on_log or _log
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", bind_port)); self.sock.settimeout(0.05)
        self.local_port = self.sock.getsockname()[1]
        self.psk = os.urandom(32)
        self.peer = None; self.locked = False; self.connected = False
        self.alive = True; self.last_rx = time.time()
        self.tx = self.base = self.rx = 0
        self.out = {}; self.wire = {}; self.sent = {}
        self.lock = threading.Lock()
        self.streams = {}; self.next_stream = 1
        self.listener = None; self.listen_port = 0
        self.motd = "Void"; self.on_frame = None
        self.send_key = self.recv_key = None; self.tx_ctr = 0
        self.top = -1; self.seen = set()
        if HAVE_CRYPTO:
            self.priv = X25519PrivateKey.generate()
            self.pub = self.priv.public_key().public_bytes_raw()
        else:
            self.priv = None; self.pub = os.urandom(32)

    def _status(self, k, info=None):
        self.log(f"status: {k} {info or ''}")
        self.on_status(k, info or {})

    def _mac(self, tag, *parts):
        return hmac.new(self.psk, tag + self.sid + b"".join(parts), hashlib.sha256).digest()[:16]

    def _derive(self, peer_pub):
        if not HAVE_CRYPTO:
            return
        shared = self.priv.exchange(X25519PublicKey.from_public_bytes(peer_pub))
        okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=self.psk,
                   info=b"voidlink-v4").derive(shared)
        up, down = okm[:32], okm[32:]
        if self.role == "host":
            self.recv_key = ChaCha20Poly1305(up); self.send_key = ChaCha20Poly1305(down)
        else:
            self.send_key = ChaCha20Poly1305(up); self.recv_key = ChaCha20Poly1305(down)
        sas = HKDF(algorithm=hashes.SHA256(), length=4, salt=self.psk,
                   info=b"sas").derive(shared + self.pub + peer_pub)
        self.sas = f"{int.from_bytes(sas, 'big') % 1000000:06d}"

    def _seal(self, seq, ack, payload=b""):
        if not HAVE_CRYPTO or not self.send_key:
            return None
        self.tx_ctr += 1
        nonce = self.tx_ctr.to_bytes(COUNTER, "big")
        pt = struct.pack("!II", seq, ack) + payload
        ct = self.send_key.encrypt(nonce, pt, self.sid + nonce)
        return nonce + ct

    def _open(self, body):
        if not HAVE_CRYPTO or not self.recv_key or len(body) < COUNTER + 16:
            return None
        nonce, ct = body[:COUNTER], body[COUNTER:]
        c = int.from_bytes(nonce, "big")
        if c <= self.top - 256 or (c <= self.top and c in self.seen):
            return None
        if c > self.top:
            self.top = c; self.seen = {x for x in self.seen if x > c - 256}
        self.seen.add(c)
        try:
            pt = self.recv_key.decrypt(nonce, ct, self.sid + nonce)
        except Exception:
            return None
        if len(pt) < 8:
            return None
        seq, ack = struct.unpack("!II", pt[:8])
        return seq, ack, pt[8:]

    def _sendraw(self, typ, body=b"", addr=None):
        dst = addr or self.peer
        if dst:
            try:
                self.sock.sendto(HDR.pack(self.sid, typ) + body, dst)
            except OSError:
                pass

    def send_frame(self, stream, kind, data=b""):
        with self.lock:
            seq = self.tx; self.tx += 1
            self.out[seq] = FRAME.pack(stream, kind, len(data)) + data
            self.wire.pop(seq, None)
        self._pump()

    def _pump(self):
        if not self.connected:
            return
        with self.lock:
            end = min(self.tx, self.base + WINDOW)
            now = time.time()
            stale = self.base in self.sent and now - self.sent[self.base] > RTO
            for seq in range(self.base, end):
                if seq not in self.sent or stale:
                    if seq not in self.wire:
                        sealed = self._seal(seq, self.rx, self.out[seq])
                        if sealed is None:
                            return
                        self.wire[seq] = HDR.pack(self.sid, T_DATA) + sealed
                    try:
                        self.sock.sendto(self.wire[seq], self.peer)
                    except OSError:
                        pass
                    self.sent[seq] = now

    def _on_ack(self, ack):
        with self.lock:
            while self.base < ack:
                self.out.pop(self.base, None); self.wire.pop(self.base, None)
                self.sent.pop(self.base, None); self.base += 1
        self._pump()

    def _handle(self, payload):
        if not self.on_frame or len(payload) < FRAME.size:
            return
        stream, kind, ln = FRAME.unpack(payload[:FRAME.size])
        self.on_frame(stream, kind, payload[FRAME.size:FRAME.size + ln])

    def _recv_loop(self):
        while self.alive:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < HDR.size:
                continue
            sid, typ = HDR.unpack(data[:HDR.size])
            if sid != self.sid:
                continue
            if self.locked and addr != self.peer:
                continue
            self.last_rx = time.time()
            body = data[HDR.size:]
            if typ == T_PUNCH:
                self._sendraw(T_PUNCH, addr=addr)
            elif typ == T_HELLO and self.role == "host":
                self.peer = addr
                self.log("host: получил HELLO от " + str(addr))
                self._sendraw(T_ACK, self.pub + self._mac(b"ack", self.pub), addr=addr)
                self._derive(body[:32])
                self.connected = self.locked = True
                self._status("connected", {"sas": getattr(self, "sas", "")})
            elif typ == T_ACK and self.role == "join":
                pub = body[:32]; mac = body[32:48]
                if not hmac.compare_digest(mac, self._mac(b"ack", pub)):
                    continue
                self.peer = addr
                self.log("join: получил ACK от " + str(addr))
                self._derive(pub)
                self.connected = self.locked = True
                self._status("connected", {"sas": getattr(self, "sas", "")})
            elif typ == T_DATA and self.connected:
                r = self._open(body)
                if not r:
                    continue
                seq, ack, payload = r
                self._on_ack(ack)
                if seq == self.rx:
                    self.rx += 1
                    self._handle(payload)
                    sealed = self._seal(0, self.rx, b"")
                    if sealed:
                        self._sendraw(T_DATA, sealed)
            elif typ == T_BYE:
                self.connected = False
                self._status("closed")
        try:
            self.sock.close()
        except OSError:
            pass

    def _tick(self):
        while self.alive:
            if self.connected:
                self._pump()
                if time.time() - self.last_rx > 90:
                    self._status("idle_timeout"); self.connected = False; self._sendraw(T_BYE)
            elif self.role == "join" and self.peer:
                self._sendraw(T_HELLO, self.pub + self._mac(b"hello", self.pub))
            time.sleep(0.02)

    def start(self):
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._tick, daemon=True).start()

    def stop(self):
        self._sendraw(T_BYE); self.alive = False
        for s in list(self.streams.values()):
            try: s.close()
            except OSError: pass
        if self.listener:
            try: self.listener.close()
            except OSError: pass
        try: self.sock.close()
        except OSError: pass

    def _pump_socket(self, stream, sock):
        try:
            while self.alive:
                data = sock.recv(MAXP)
                if not data:
                    break
                self.send_frame(stream, K_DATA, data)
        except OSError:
            pass
        finally:
            self.send_frame(stream, K_CLOSE)
            try: sock.close()
            except OSError: pass
            self.streams.pop(stream, None)

    def host_attach(self, lan_port):
        self.log(f"host_attach к порту игры {lan_port}")
        def on_frame(stream, kind, data):
            if kind == K_OPEN:
                try:
                    s = socket.create_connection(("127.0.0.1", lan_port), timeout=8)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.streams[stream] = s
                    threading.Thread(target=self._pump_socket, args=(stream, s), daemon=True).start()
                except OSError:
                    self.send_frame(stream, K_CLOSE)
            elif kind == K_DATA and stream in self.streams:
                try: self.streams[stream].sendall(data)
                except OSError: pass
            elif kind == K_CLOSE and stream in self.streams:
                s = self.streams.pop(stream)
                try: s.close()
                except OSError: pass
        self.on_frame = on_frame

    def join_attach(self, motd):
        self.motd = motd
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0)); self.listener.listen(8)
        self.listen_port = self.listener.getsockname()[1]
        def on_frame(stream, kind, data):
            s = self.streams.get(stream)
            if kind == K_DATA and s:
                try: s.sendall(data)
                except OSError: pass
            elif kind == K_CLOSE and s:
                self.streams.pop(stream, None)
                try: s.close()
                except OSError: pass
        self.on_frame = on_frame

        def accept():
            while self.alive:
                try: s, _ = self.listener.accept()
                except OSError: break
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                stream = self.next_stream; self.next_stream += 2
                self.streams[stream] = s
                self.send_frame(stream, K_OPEN)
                threading.Thread(target=self._pump_socket, args=(stream, s), daemon=True).start()
        threading.Thread(target=accept, daemon=True).start()
        threading.Thread(target=self._beacon, daemon=True).start()
        self.log(f"join_attach: прокси слушает 127.0.0.1:{self.listen_port}")
        return self.listen_port

    def _beacon(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        pkt = f"[MOTD]{self.motd}[/MOTD][AD]{self.listen_port}[/AD]".encode()
        while self.alive:
            try: s.sendto(pkt, ("127.0.0.1", 4445))
            except OSError: pass
            time.sleep(1.5)
        s.close()


def host(name, lan_port, bind_port=0, on_status=None, on_log=None, allow_stun=True):
    """Создаёт хост-туннель. lan_port — порт игры, который ввёл пользователь."""
    if not allow_stun:
        raise RuntimeError("STUN выключен в настройках")
    if not isinstance(lan_port, int) or lan_port <= 0 or lan_port > 65535:
        raise RuntimeError("Некорректный порт игры")
    pub = _stun()
    link = Tunnel("host", os.urandom(4), bind_port, on_status, on_log)
    link.host_attach(lan_port)
    link.start()
    code = make_code({"v": PROTO, "sid": base64.b64encode(link.sid).decode(),
                      "psk": base64.b64encode(link.psk).decode(),
                      "ep": [list(pub), [_local_ip(), link.local_port]],
                      "exp": time.time() + CODE_TTL, "name": name, "lan": lan_port})
    link.log(f"host: код создан, внеш. {pub}, порт игры {lan_port}")
    return link, code


def join(code, motd=None, on_status=None, on_log=None):
    info = read_code(code)
    link = Tunnel("join", base64.b64decode(info["sid"]), 0, on_status, on_log)
    link.psk = base64.b64decode(info["psk"])
    port = link.join_attach(motd or ("Void · " + info.get("name", "мир")))
    link.peer = tuple(info["ep"][0])
    link.start()

    def punch():
        targets = [tuple(e) for e in info["ep"]]
        t0 = time.time()
        link.log(f"join: пробиваюсь к {targets}")
        while link.alive and not link.connected and time.time() - t0 < 30:
            for t in targets:
                try:
                    link.sock.sendto(HDR.pack(link.sid, T_HELLO) + link.pub +
                                     link._mac(b"hello", link.pub), t)
                except OSError:
                    pass
            time.sleep(0.2)
        if not link.connected:
            link.log("join: не пробился за 30с (вероятно симметричный NAT)")
            link._status("punch_failed")
    threading.Thread(target=punch, daemon=True).start()
    return link, port