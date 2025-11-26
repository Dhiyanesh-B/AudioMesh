import sys, socket, threading, json, time, emoji, string, random, os
from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QObject
from PyQt6.QtGui import QFont

import sounddevice as sd
import pyaudio
import numpy as np

# ======================= CONFIG =======================
BROADCAST_PORT = 5000
CONTROL_PORT   = 5010
AUDIO_PORT     = 5012

# Use the same audio params on both sides to avoid any resampling noise
CHUNK          = 512          # frames per chunk (matches your sender.py)
CHANNELS       = 2
RATE           = 48000        # matches your sender.py
DTYPE_SD       = 'int16'      # sounddevice dtype
FORMAT_PYA     = pyaudio.paInt16  # PyAudio dtype
CONFIG_FILE    = "user_config.json"

# ======================= NETWORK MANAGER =======================
class NetworkManager(QObject):
    device_discovered = pyqtSignal(str, str, str)  # dev_id, name, ip
    device_removed    = pyqtSignal(str)
    chat_received     = pyqtSignal(str, str, str)  # sender, msg, ts
    sender_status_changed = pyqtSignal(bool, str)  # active, name

    def __init__(self):
        super().__init__()
        self.local_ip = self._get_local_ip()
        self.local_name = ""
        self.role = None
        self.sender_ip: Optional[str] = None
        self.sender_name = ""
        self.devices: Dict[str, dict] = {}       # dev_id -> {ip,name,status}
        self.connections: Dict[str, socket.socket] = {}  # dev_id -> UDP socket
        self._lock = threading.Lock()

        self._presence_thread = None
        self._discovery_thread = None

        # listeners
        self._start_broadcast_listener()
        self._start_control_listener()

    def _get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
        except Exception:
            return '127.0.0.1'
        finally:
            s.close()

    def _reset(self):
        with self._lock:
            for s in self.connections.values():
                try: s.close()
                except: pass
            self.connections.clear()
            self.devices.clear()
            self.sender_ip = None
            self.sender_name = ""

    # ---- Roles
    def start_as_sender(self, name: str) -> bool:
        self._reset()
        self.role = 'sender'
        self.local_name = name
        self.sender_status_changed.emit(True, name)
        self._presence_thread = threading.Thread(target=self._broadcast_presence, daemon=True)
        self._presence_thread.start()
        return True

    def start_as_receiver(self, name: str):
        self._reset()
        self.role = 'receiver'
        self.local_name = name
        self.sender_status_changed.emit(False, "")
        self._discovery_thread = threading.Thread(target=self._discover_sender, daemon=True)
        self._discovery_thread.start()

    # ---- Discovery & presence
    def _broadcast_presence(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.role == 'sender':
            msg = {"type":"presence","name":self.local_name,"ip":self.local_ip,"role":"sender","audio_port":AUDIO_PORT}
            try: sock.sendto(json.dumps(msg).encode(), ('<broadcast>', BROADCAST_PORT))
            except: pass
            time.sleep(1.0)

    def _discover_sender(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.role == 'receiver':
            msg = {"type":"discover","name":self.local_name,"ip":self.local_ip}
            try: sock.sendto(json.dumps(msg).encode(), ('<broadcast>', BROADCAST_PORT))
            except: pass
            time.sleep(2.0)

    def _start_broadcast_listener(self):
        def run():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(('', BROADCAST_PORT))
            while True:
                try:
                    data,(ip,_) = sock.recvfrom(2048)
                    if ip == self.local_ip: continue
                    msg = json.loads(data.decode(errors='ignore'))
                    if msg.get("type")=="presence" and msg.get("role")=="sender":
                        if self.role == "receiver":
                            self.sender_ip = ip
                            self.sender_name = msg.get("name","Sender")
                            self.sender_status_changed.emit(True, self.sender_name)
                            self._send_join_to_sender()  # register so sender sees us
                except: pass
        threading.Thread(target=run, daemon=True).start()

    def _send_join_to_sender(self):
        if not self.sender_ip: return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        payload = {"type":"join","name":self.local_name,"role":"receiver"}
        try: sock.sendto(json.dumps(payload).encode(), (self.sender_ip, CONTROL_PORT))
        except: pass

    # ---- Control & chat
    def _start_control_listener(self):
        def run():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', CONTROL_PORT))
            while True:
                try:
                    data,(ip,_) = sock.recvfrom(4096)
                    msg = json.loads(data.decode(errors='ignore'))
                    t = msg.get("type","")
                    if t == "join" and self.role == 'sender':
                        dev_id = f"{ip}:{msg.get('name','')}"
                        with self._lock:
                            self.devices[dev_id] = {"ip":ip,"name":msg.get("name",""),"status":"available"}
                        self.device_discovered.emit(dev_id, msg.get("name",""), ip)

                    elif t == "chat":
                        # Always show message locally
                        self.chat_received.emit(msg.get("sender",""), msg.get("message",""), msg.get("time",""))
                        # If we are the sender, rebroadcast to all receivers (so it's truly bidirectional)
                        if self.role == 'sender':
                            rebroadcast = json.dumps(msg).encode()
                            sender_ip = ip  # address of the receiver who sent this chat
                            for dev_id, s in list(self.connections.items()):
                                rip = self.devices.get(dev_id, {}).get("ip")
                                if not rip or rip == sender_ip:
                                    continue
                                try:
                                    s.sendto(rebroadcast, (rip, CONTROL_PORT))
                                except:
                                    pass
                except: pass
        threading.Thread(target=run, daemon=True).start()

    def connect_to_receivers(self, selected: List[str]):
        # Create per-receiver UDP sockets for CONTROL/AUDIO sends
        for dev_id in selected:
            ip = self.devices.get(dev_id,{}).get("ip")
            if not ip or dev_id in self.connections: continue
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.connections[dev_id] = s
            # optional small ACK
            try: s.sendto(json.dumps({"type":"ack","from":self.local_name}).encode(), (ip, CONTROL_PORT))
            except: pass

    def disconnect_all(self):
        with self._lock:
            for s in self.connections.values():
                try: s.close()
                except: pass
            self.connections.clear()

    def send_chat(self, message: str):
        payload = {"type":"chat","sender":self.local_name,"message":message,"time":time.strftime("%H:%M")}
        if self.role == 'sender':
            # sender -> all receivers
            for dev_id, s in list(self.connections.items()):
                ip = self.devices.get(dev_id,{}).get("ip")
                if not ip: continue
                try: s.sendto(json.dumps(payload).encode(), (ip, CONTROL_PORT))
                except: pass
        elif self.role == 'receiver' and self.sender_ip:
            # receiver -> sender (who rebroadcasts to everyone)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try: s.sendto(json.dumps(payload).encode(), (self.sender_ip, CONTROL_PORT))
            except: pass

# ======================= AUDIO MANAGER =======================
class AudioManager:

    def __init__(self, network: NetworkManager):
        self.network = network
        self.capture_stream: Optional[sd.InputStream] = None
        self.is_streaming = False
        self.loopback_index = self._find_loopback()
        self.receiver: Optional['UDPReceiverPlayer'] = None

    def _find_loopback(self) -> Optional[int]:
        """Find Windows loopback device: 'Stereo Mix' or 'Loopback' or a WASAPI loopback device name."""
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        candidate = None
        for i, dev in enumerate(devices):
            name = str(dev['name'])
            lname = name.lower()
            if "stereo mix" in lname or "loopback" in lname:
                candidate = i; break
        if candidate is not None:
            return candidate
        # fallback to default input if present
        try:
            return sd.default.device[0]
        except Exception:
            return None

    # ---- Sender capture (sounddevice)
    def start_capture(self):
        if self.is_streaming:
            return
        if self.loopback_index is None:
            QMessageBox.warning(None, "Loopback not found",
                                "No loopback device found. Enable 'Stereo Mix' or a virtual cable (VB-Cable).")
            return

        self.is_streaming = True

        def callback(indata, frames, time_info, status):
            # EXACT: send raw bytes of int16 frames to each receiver (no separate threads)
            if not self.is_streaming:
                return
            raw = indata.tobytes()
            for dev_id, sock in list(self.network.connections.items()):
                ip = self.network.devices.get(dev_id,{}).get("ip")
                if not ip: continue
                try:
                    sock.sendto(raw, (ip, AUDIO_PORT))
                except:
                    pass
            time.sleep(0.0005)
            
        self.capture_stream = sd.InputStream(
            samplerate=RATE,
            channels=CHANNELS,
            dtype=DTYPE_SD,
            blocksize=CHUNK,
            device=self.loopback_index,
            latency='high',                 # keep as in your sender.py
            callback=callback
        )
        self.capture_stream.start()

    def stop_capture(self):
        self.is_streaming = False
        if self.capture_stream:
            try:
                self.capture_stream.stop()
                self.capture_stream.close()
            except:
                pass
            self.capture_stream = None

    # ---- Receiver playback (PyAudio)
    def start_playback(self):
        if self.receiver:  # already running
            return
        self.receiver = UDPReceiverPlayer()
        self.receiver.start()

    def stop_playback(self):
        if self.receiver:
            self.receiver.stop()
            self.receiver = None

class UDPReceiverPlayer:
    """receiver.py: PyAudio output, single tight UDP loop writing int16 frames."""
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=FORMAT_PYA,
            channels=CHANNELS,
            rate=RATE,
            output=True,
            frames_per_buffer=CHUNK
        )
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', AUDIO_PORT))
        self.sock.settimeout(1.0)

        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        self.running = True
        # one lean thread to avoid blocking the UI
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        try: self.sock.close()
        except: pass
        try:
            self.stream.stop_stream()
            self.stream.close()
        except: pass
        try: self.p.terminate()
        except: pass

    def _loop(self):
        # identical to your receiver.py main loop, adapted to class
        while self.running:
            try:
                data, _ = self.sock.recvfrom(CHUNK * CHANNELS * 2)  # int16 bytes
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                self.stream.write(data)
            except Exception:
                # ignore occasional write issues on close
                pass

# ======================= CHAT MANAGER =======================
class ChatManager(QObject):
    message_sent = pyqtSignal(str, str, bool)

    def __init__(self, network: NetworkManager):
        super().__init__()
        self.network = network
        self.network.chat_received.connect(self._on_chat)

    def send(self, text: str):
        text = emoji.emojize(text, language='alias')
        self.network.send_chat(text)
        self.message_sent.emit(self.network.local_name, text, True)

    def _on_chat(self, sender: str, msg: str, ts: str):
        self.message_sent.emit(sender, msg, False)

# ======================= CONFIG HELPERS =======================
def load_user_name() -> str:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get("name"): return data["name"]
        except: pass
    name = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(4))
    save_user_name(name)
    return name

def save_user_name(name: str):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump({"name": name}, f)
    except: pass

# ======================= MAIN UI =======================
class MainWindow(QMainWindow):
    def __init__(self, network: NetworkManager, audio: AudioManager, chat: ChatManager):
        super().__init__()
        self.network = network
        self.audio = audio
        self.chat = chat
        self.setWindowTitle("LAN Audio Sync")
        self.resize(1000, 680)
        self.setStyleSheet("QMainWindow { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #0a0e17, stop:1 #1a1f2e); color:#e0e0e0; }")

        self._init_ui()
        self.network.sender_status_changed.connect(self._on_sender_status)
        self.chat.message_sent.connect(self._on_chat_msg)
        self.network.device_discovered.connect(self._add_device)

        self.name_input.setText(load_user_name())

    def _init_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        left = self._left_panel()
        right = self._right_panel()

        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([300, 700])
        layout.addWidget(splitter)

    def _left_panel(self):
        panel = QWidget(); panel.setFixedWidth(300)
        panel.setStyleSheet("background:#11151c; border-right:1px solid #2a2f3b;")
        lay = QVBoxLayout(panel)

        title = QLabel("LAN Audio Sync")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:18px; font-weight:bold; color:#00ff88; padding:20px;")
        lay.addWidget(title)

        self.role_group = QButtonGroup()
        sender_btn = self._glow_btn("Sender Mode", "#ff3b5f")
        receiver_btn = self._glow_btn("Receiver Mode", "#00d4ff")
        self.role_group.addButton(sender_btn, 1)
        self.role_group.addButton(receiver_btn, 2)
        self.role_group.buttonClicked.connect(self._role_selected)

        lay.addWidget(sender_btn)
        lay.addWidget(receiver_btn)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Enter your name…")
        self.name_input.setStyleSheet("padding:12px; border-radius:8px; background:#1e2533;")
        self.name_input.textChanged.connect(self._on_name_changed)
        lay.addWidget(self.name_input)

        self.status_lbl = QLabel("Choose a mode")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setStyleSheet("color:#888; padding:10px; font-size:13px;")
        self.status_lbl.setWordWrap(True)
        lay.addWidget(self.status_lbl)

        lay.addStretch()
        return panel

    def _right_panel(self):
        self.stack = QStackedWidget()
        self.stack.addWidget(self._sender_ui())
        self.stack.addWidget(self._receiver_ui())
        return self.stack

    def _sender_ui(self):
        w = QWidget(); lay = QVBoxLayout(w)
        hdr = QLabel("Sender Dashboard")
        hdr.setStyleSheet("font-size:20px; color:#00ff88; padding:15px;")
        lay.addWidget(hdr)

        self.device_list = QListWidget()
        self.device_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.device_list.setStyleSheet("background:#1a1f2e; border:none; border-radius:8px;")
        lay.addWidget(self.device_list, 1)

        btns = QHBoxLayout()
        self.start_btn = self._glow_btn("Start Stream", "#00ff88")
        self.stop_btn  = self._glow_btn("Stop Stream",  "#ff3b5f")
        self.refresh_btn = self._glow_btn("Refresh", "#ffcc00")
        self.start_btn.clicked.connect(self._start_stream)
        self.stop_btn.clicked.connect(self._stop_stream)
        self.refresh_btn.clicked.connect(self._refresh_devices)
        btns.addWidget(self.start_btn); btns.addWidget(self.stop_btn); btns.addWidget(self.refresh_btn)
        lay.addLayout(btns)

        self.sender_chat = QTextEdit(); self.sender_chat.setReadOnly(True)
        self.sender_chat.setStyleSheet("background:#11151c; border-radius:8px;")
        lay.addWidget(self.sender_chat, 2)

        self.sender_input = QLineEdit()
        self.sender_input.setPlaceholderText("Type a message… (:smile:)")
        self.sender_input.returnPressed.connect(lambda: self._send_chat(self.sender_input))
        lay.addWidget(self.sender_input)
        return w

    def _receiver_ui(self):
        w = QWidget(); lay = QVBoxLayout(w)

        self.receiver_chat = QTextEdit(); self.receiver_chat.setReadOnly(True)
        self.receiver_chat.setStyleSheet("background:#11151c; border-radius:8px;")
        lay.addWidget(self.receiver_chat, 2)

        self.receiver_input = QLineEdit()
        self.receiver_input.setPlaceholderText("Chat…")
        self.receiver_input.returnPressed.connect(lambda: self._send_chat(self.receiver_input))
        lay.addWidget(self.receiver_input)
        return w

    # --- helpers / styling
    def _glow_btn(self, text, color):
        btn = QPushButton(text)
        btn.setStyleSheet(f"""
            QPushButton {{ background:{color}; color:white; border:none; padding:12px; border-radius:12px; font-weight:bold; }}
            QPushButton:hover {{ background:{self._lighten(color)}; }}
            QPushButton:pressed {{ background:{self._darken(color)}; }}
        """)
        btn.anim = QPropertyAnimation(btn, b"geometry")
        btn.anim.setDuration(200)
        btn.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        def enter(_): self._animate(btn, 1.0, 1.05)
        def leave(_): self._animate(btn, 1.05, 1.0)
        btn.enterEvent = enter; btn.leaveEvent = leave
        return btn

    def _animate(self, btn, start, end):
        g = btn.geometry()
        dw = int(g.width() * (end - start))
        dh = int(g.height() * (end - start))
        ng = g.adjusted(-dw//2, -dh//2, dw//2, dh//2)
        btn.anim.stop(); btn.anim.setStartValue(g); btn.anim.setEndValue(ng); btn.anim.start()

    def _lighten(self, c):
        r = min(255, int(c[1:3], 16) + 50)
        g = min(255, int(c[3:5], 16) + 50)
        b = min(255, int(c[5:7], 16) + 50)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _darken(self, c):
        r = max(0, int(c[1:3], 16) - 40)
        g = max(0, int(c[3:5], 16) - 40)
        b = max(0, int(c[5:7], 16) - 40)
        return f"#{r:02x}{g:02x}{b:02x}"

    # --- events
    def _role_selected(self, btn):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Name Required", "Please enter your name before proceeding.")
            return
        save_user_name(name)

        rid = self.role_group.id(btn)
        if rid == 1:  # Sender
            self.network.start_as_sender(name)
            self.stack.setCurrentIndex(0)
            self.audio.stop_playback()
            self.status_lbl.setText(f"Sender Mode Active — {name}")
        else:        # Receiver
            self.network.start_as_receiver(name)
            self.stack.setCurrentIndex(1)
            self.audio.start_playback()
            self.status_lbl.setText(f"Receiver Mode Active — Searching for Sender…")

    def _on_sender_status(self, active: bool, name: str):
        if self.network.role == "receiver":
            self.status_lbl.setText(f"Connected to: {name}" if active else "Receiver Mode Active — Searching for Sender…")

    def _add_device(self, dev_id, name, ip):
        # prevent duplicates
        for i in range(self.device_list.count()):
            if self.device_list.item(i).data(Qt.ItemDataRole.UserRole) == dev_id:
                return
        it = QListWidgetItem(f"{name} ({ip})")
        it.setData(Qt.ItemDataRole.UserRole, dev_id)
        self.device_list.addItem(it)

    def _refresh_devices(self):
        self.device_list.clear()
        # Receivers will re-join automatically as presence continues.

    def _start_stream(self):
        sel = [i.data(Qt.ItemDataRole.UserRole) for i in self.device_list.selectedItems()]
        if not sel:
            QMessageBox.warning(self, "Error", "Select at least one receiver.")
            return
        self.network.connect_to_receivers(sel)
        self.audio.start_capture()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _stop_stream(self):
        self.audio.stop_capture()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _send_chat(self, line_edit: QLineEdit):
        txt = line_edit.text().strip()
        if not txt: return
        self.chat.send(txt)
        line_edit.clear()
        
    def _on_name_changed(self, text):
        """Save updated name to config when user types in name box."""
        t = text.strip()
        if t:
            save_user_name(t)

    def _on_chat_msg(self, sender, msg, is_me):
        color = "#00ff88" if is_me else "#e0e0e0"
        ts = time.strftime("%H:%M")
        html = f'<span style="color:#888">[{ts}] </span><span style="color:{color};font-weight:{"bold" if is_me else "normal"}">{sender}:</span> {emoji.demojize(msg)}'
        if self.stack.currentIndex() == 0:
            self.sender_chat.append(html)
        else:
            self.receiver_chat.append(html)

# ======================= MAIN =======================
def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    network = NetworkManager()
    audio = AudioManager(network)
    chat = ChatManager(network)

    win = MainWindow(network, audio, chat)
    win.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
