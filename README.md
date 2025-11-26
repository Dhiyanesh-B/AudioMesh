# **AudioMesh**

### *Real-Time Multi-Device LAN Audio Streaming with PyQt6*

AudioMesh is a desktop application that lets you **broadcast your system audio in real time** across multiple devices on the same LAN.
It uses **UDP sockets**, **WASAPI loopback capture**, and **PyAudio/SoundDevice playback** to deliver synchronized audio with minimal latency.

The app includes a modern **PyQt6 interface**, automatic device discovery, and a built-in LAN chat system.

---

## 🚀 **Features**

### 🎵 Real-Time Audio Streaming

* Capture system audio using **WASAPI loopback**
* Stream raw PCM frames over **UDP**
* Synchronized playback on all connected receivers

### 🔧 Automatic Device Discovery

* Sender broadcasts presence over LAN
* Receivers auto-detect and connect
* No manual IP entry needed

### 💬 Built-in LAN Chat

* Sender ↔ Receiver communication
* Broadcast chat to all connected devices

### 🖥️ Modern PyQt6 UI

* Sender/Receiver mode selection
* Animated buttons & gradient styling
* Device list panel (auto-refresh)
* Real-time chat panel

### ⚡ Low Latency Pipeline

* Uses **CHUNK = 512**, 48 kHz, Stereo
* SoundDevice for capture, PyAudio for playback
* Tight-threaded UDP loops for smooth streaming

---

## 🛠️ **Tech Stack**

* **Python 3**
* **PyQt6** – UI
* **Socket Programming (UDP)** – streaming & control
* **SoundDevice** – loopback audio capture
* **PyAudio** – playback engine
* **Threading** – async listeners & streaming
* **JSON** – control message protocol

---

## 🔌 **How It Works**

### Sender Mode

1. Captures system audio via WASAPI
2. Sends presence packets (broadcast)
3. Streams audio frames to selected receivers
4. Rebroadcasts chat messages

### Receiver Mode

1. Listens for sender presence
2. Auto-connects
3. Plays audio via PyAudio
4. Sends chat messages back to sender

---

## 🖼️ Screenshots

###  Sender Dashboard  
<img src="ScreenShots/sender dashboard.png" alt="Login Page" width="600">

### Receiver Dashboard 
<img src="ScreenShots/receiver dashboard.png" alt="Welcome Page" width="600">


---

## 📥 **Installation**

### 1. Install dependencies

```bash
pip install pyqt6 sounddevice pyaudio numpy emoji
```

If PyAudio fails:

```bash
pip install pipwin
pipwin install pyaudio
```

### 2. Run the app

```bash
python main.py
```

---

## 🖧 **Usage**

### Sender

* Select **Sender Mode**
* Choose devices
* Click **Start Stream**

### Receiver

* Select **Receiver Mode**
* Auto-connects to sender
* Audio plays instantly

### Chat

* Type messages on either side
* All devices receive the broadcast

---

## 🧩 **Future Improvements**

* Audio compression (Opus / PCM16 → Opus)
* Jitter buffers for tighter sync
* Volume control per receiver
* Network quality indicators

### Team Members
- Parikshit V
- Dhiyanesh B

Email: dhiyanesh.b.19@gmail.com
