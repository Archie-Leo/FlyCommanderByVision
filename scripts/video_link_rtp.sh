#!/usr/bin/env bash
# Independent RK3576 left-eye RTP/H.264 video link. No AI or flight control.
set -euo pipefail

camera=/dev/video73
host=192.168.1.16
port=5600
fps=30
bitrate=4000
width=1280
height=960
rotate=0

usage() {
    cat <<'EOF'
Usage: bash scripts/video_link_rtp.sh [options]
  --camera DEVICE        UVC SBS camera (default /dev/video73)
  --host HOST            Windows receiver address (default 192.168.1.16)
  --port PORT            RTP/UDP port (default 5600)
  --fps FPS              Output frame rate, 1..60 (default 30)
  --bitrate KBPS         H.264 target in kbit/s (default 4000)
  --resolution WxH       1280x960 or 960x720 (default 1280x960)
  --rotate-180           Rotate the left eye in the hardware encoder
EOF
}

while (($#)); do
    case "$1" in
        --camera|--host|--port|--fps|--bitrate|--resolution)
            (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
            case "$1" in
                --camera) camera=$2 ;;
                --host) host=$2 ;;
                --port) port=$2 ;;
                --fps) fps=$2 ;;
                --bitrate) bitrate=$2 ;;
                --resolution) IFS=x read -r width height <<< "$2" ;;
            esac
            shift 2 ;;
        --rotate-180) rotate=180; shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -c "$camera" ]] || { echo "Camera device unavailable: $camera" >&2; exit 2; }
[[ -n "$host" ]] || { echo "Host is required" >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || { echo "Invalid port" >&2; exit 2; }
[[ "$fps" =~ ^[0-9]+$ ]] && ((fps >= 1 && fps <= 60)) || { echo "Invalid FPS" >&2; exit 2; }
[[ "$bitrate" =~ ^[0-9]+$ ]] && ((bitrate >= 500 && bitrate <= 20000)) || { echo "Invalid bitrate" >&2; exit 2; }
[[ "$width:$height" == 1280:960 || "$width:$height" == 960:720 ]] || {
    echo "Resolution must be 1280x960 or 960x720" >&2; exit 2;
}
command -v gst-launch-1.0 >/dev/null || { echo "GStreamer is required" >&2; exit 2; }
gst-inspect-1.0 mpph264enc >/dev/null || { echo "Rockchip MPP H.264 encoder unavailable" >&2; exit 2; }
gst-inspect-1.0 mppjpegdec >/dev/null || { echo "Rockchip MPP JPEG decoder unavailable" >&2; exit 2; }

# The queue holds one compressed camera frame and drops older frames if decode
# or encode lags. Rate reduction happens before JPEG decode.
pipeline=(
    gst-launch-1.0 -e
    v4l2src "device=$camera" io-mode=2 do-timestamp=true
    ! "image/jpeg,width=2560,height=960,framerate=60/1"
    ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream
    ! videorate drop-only=true
    ! "image/jpeg,framerate=$fps/1"
    ! jpegparse
    ! mppjpegdec
    ! videoconvert
    ! video/x-raw,format=NV12
    ! videocrop right=1280
    ! video/x-raw,width=1280,height=960
)
if [[ "$width" == 960 ]]; then
    pipeline+=( ! videoscale ! video/x-raw,width=960,height=720 )
fi
pipeline+=(
    ! mpph264enc "rotation=$rotate" "bps=$((bitrate * 1000))" gop=30
      rc-mode=cbr max-pending=1 header-mode=each-idr profile=baseline
    ! h264parse config-interval=-1
    ! rtph264pay pt=96 config-interval=-1 mtu=1200
    ! udpsink "host=$host" "port=$port" sync=false async=false
)

printf 'Video link: LEFT %sx%s @ %s FPS, %s kbit/s, rotate %s, RTP/UDP %s:%s\n' \
    "$width" "$height" "$fps" "$bitrate" "$rotate" "$host" "$port" >&2
exec "${pipeline[@]}"
