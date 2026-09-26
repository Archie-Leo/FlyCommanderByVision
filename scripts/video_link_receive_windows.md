# RK3576 video link receiver (Windows)

From a Windows checkout of this repository, start the receiver before the sender:

```powershell
ffplay -protocol_whitelist file,udp,rtp -fflags nobuffer -flags low_delay -framedrop -probesize 32 -analyzeduration 0 -max_delay 0 -sync ext -i configs/video_link.sdp
```

This PC also has `ffplay.exe` at `D:\K230IDE\share\qtcreator\ffmpeg\windows\bin\ffplay.exe`; use that full path if `ffplay` is not on `PATH`. The SDP listens on UDP port 5600. If the sender uses another port, change the `m=video` port in `configs/video_link.sdp` to match.

On the RK3576, run:

```bash
cd ~/FlyCommanderByVision
bash scripts/video_link_rtp.sh --camera /dev/video73 --host 192.168.1.16 --port 5600 --fps 30 --bitrate 4000 --rotate-180
```

The sender uses only the left half of the 2560x960 MJPEG image. It has no AI overlay or PX4 connection. Stop it with Ctrl+C before starting another camera consumer. Press `q` in ffplay to close the receiver.
