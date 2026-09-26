"""Hardware fan-out topology and input contract checks."""

from types import SimpleNamespace

import pytest

from scripts.run_rk3576_fanout import Counters, LatestFrameSlot, pipeline_description


def args(**overrides):
    value = dict(camera="/dev/video73", camera_fps=60, video_fps=30,
                 host="192.168.1.16", port=5600, bitrate_kbps=4000)
    value.update(overrides)
    return SimpleNamespace(**value)


def test_one_camera_one_decoder_two_bounded_branches():
    pipeline = pipeline_description(args())
    assert pipeline.count("v4l2src ") == 1
    assert pipeline.count("mppjpegdec ") == 1
    assert pipeline.count("tee name=decoded") == 1
    assert pipeline.count("leaky=downstream") == 3
    assert "videocrop right=1280" in pipeline
    assert "mpph264enc name=encoder rotation=180 bps=4000000" in pipeline
    assert "video/x-raw,format=BGR,width=2560,height=960" in pipeline
    assert "appsink name=ai_sink max-buffers=1 drop=true sync=false" in pipeline
    assert "/fmu/" not in pipeline


def test_async_ai_branch_keeps_one_camera_and_nv12_latest_sink():
    pipeline = pipeline_description(args(async_perception=True))
    assert pipeline.count("v4l2src ") == 1
    assert pipeline.count("mppjpegdec ") == 1
    assert "decoded. ! queue name=q_ai" in pipeline
    assert "video/x-raw,format=NV12,width=2560,height=960 ! appsink" in pipeline
    assert "videoconvert" not in pipeline


def test_latest_frame_slot_replaces_unread_sample_and_closes():
    slot = LatestFrameSlot()
    first, second = object(), object()
    assert slot.publish(first)
    assert slot.publish(second)
    assert slot.published == 2
    assert slot.replaced == 1
    assert slot.take()[0] is second
    assert slot.latest is None
    slot.close()
    assert not slot.publish(first)
    assert slot.take() is None


@pytest.mark.parametrize("bad", [dict(camera="/dev/video73 ! fake"),
                                    dict(host="192.168.1.16 ! fake"),
                                    dict(port=0), dict(camera_fps=45),
                                    dict(video_fps=61)])
def test_bad_pipeline_options_rejected(bad):
    with pytest.raises(ValueError):
        pipeline_description(args(**bad))


def test_jpeg_parser_timestamp_is_bound_to_decoder_pts():
    class Buffer:
        pts = 123456789

    class Info:
        def get_buffer(self):
            return Buffer()

    counters = Counters()
    counters.source_probe(None, Info())
    counters.compressed_probe(None, Info())
    counters.decoder_probe(None, Info())
    source_id, delivery_ns = counters.source_for(Buffer.pts)
    assert source_id == 0
    assert delivery_ns > 0
    assert counters.source_count == counters.compressed_count == counters.decoded_count == 1
    assert counters.probe_error is None
