// FlyCommanderByVision native bridge for Rockchip Model Zoo v2.3.2.
// Official decode/NMS is compiled directly from examples/yolov8_pose/cpp/postprocess.cc
// at bad6c7334531becaf90a561988519b7bec34d0ab (Apache-2.0).
// Changes here: BGR/OpenCV letterbox input, lifecycle/ABI and result validation;
// the upstream decode/NMS implementation and thresholds are unchanged.
#include "pose_capi.h"

#include "yolov8-pose.h"
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>

namespace {
thread_local std::string error;
using Clock = std::chrono::steady_clock;
double elapsed_ms(Clock::time_point a, Clock::time_point b) {
    return std::chrono::duration<double, std::milli>(b - a).count();
}
int fail(const std::string& message) { error = message; return -1; }

struct Runtime {
    rknn_app_context_t app{};
    std::array<rknn_tensor_attr, 1> input_attrs{};
    std::array<rknn_tensor_attr, 4> output_attrs{};
    cv::Mat rgb;
    cv::Mat resized;
    cv::Mat canvas;
    ~Runtime() { if (app.rknn_ctx) rknn_destroy(app.rknn_ctx); }
};

bool attr_shape(const rknn_tensor_attr& attr, int c, int h, int w) {
    return attr.n_dims == 4 && attr.dims[0] == 1 && attr.dims[1] == c &&
           attr.dims[2] == h && attr.dims[3] == w;
}
} // namespace

extern "C" int fcv_pose_abi_version(void) { return FCV_POSE_ABI_VERSION; }
extern "C" const char* fcv_pose_last_error(void) { return error.c_str(); }

extern "C" void* fcv_pose_create(const char* model_path) {
    error.clear();
    if (!model_path || !*model_path) { fail("RKNN Pose model path missing"); return nullptr; }
    try {
        auto runtime = std::make_unique<Runtime>();
        int rc = rknn_init(&runtime->app.rknn_ctx, const_cast<char*>(model_path), 0, 0, nullptr);
        if (rc != RKNN_SUCC) { fail("rknn_init failed: " + std::to_string(rc)); return nullptr; }
        rc = rknn_query(runtime->app.rknn_ctx, RKNN_QUERY_IN_OUT_NUM,
                        &runtime->app.io_num, sizeof(runtime->app.io_num));
        if (rc != RKNN_SUCC || runtime->app.io_num.n_input != 1 || runtime->app.io_num.n_output != 4) {
            fail("RKNN Pose requires one input and four outputs"); return nullptr;
        }
        runtime->input_attrs[0].index = 0;
        rc = rknn_query(runtime->app.rknn_ctx, RKNN_QUERY_INPUT_ATTR,
                        &runtime->input_attrs[0], sizeof(rknn_tensor_attr));
        if (rc != RKNN_SUCC) { fail("RKNN input attribute query failed"); return nullptr; }
        const auto& in = runtime->input_attrs[0];
        if (in.n_dims != 4 || in.dims[0] != 1 || in.dims[1] != 640 || in.dims[2] != 640 ||
            in.dims[3] != 3 || in.fmt != RKNN_TENSOR_NHWC || in.type != RKNN_TENSOR_INT8) {
            fail("RKNN Pose input tensor is not validated 1x640x640x3 INT8 NHWC"); return nullptr;
        }
        for (int i = 0; i < 4; ++i) {
            runtime->output_attrs[i].index = i;
            rc = rknn_query(runtime->app.rknn_ctx, RKNN_QUERY_OUTPUT_ATTR,
                            &runtime->output_attrs[i], sizeof(rknn_tensor_attr));
            if (rc != RKNN_SUCC) { fail("RKNN output attribute query failed"); return nullptr; }
        }
        for (int i = 0; i < 3; ++i) {
            const int size = 80 >> i;
            const auto& out = runtime->output_attrs[i];
            if (!attr_shape(out, 65, size, size) || out.type != RKNN_TENSOR_INT8 ||
                out.qnt_type != RKNN_TENSOR_QNT_AFFINE_ASYMMETRIC || !std::isfinite(out.scale) || out.scale <= 0) {
                fail("RKNN Pose detection tensor shape/type/quantization mismatch"); return nullptr;
            }
        }
        const auto& kp = runtime->output_attrs[3];
        if (kp.n_dims != 4 || kp.dims[0] != 1 || kp.dims[1] != 17 || kp.dims[2] != 3 ||
            kp.dims[3] != 8400 || kp.type != RKNN_TENSOR_FLOAT16) {
            fail("RKNN Pose keypoint tensor is not 1x17x3x8400 FP16"); return nullptr;
        }
        runtime->app.input_attrs = runtime->input_attrs.data();
        runtime->app.output_attrs = runtime->output_attrs.data();
        runtime->app.model_width = 640;
        runtime->app.model_height = 640;
        runtime->app.model_channel = 3;
        runtime->app.is_quant = true;
        runtime->canvas.create(640, 640, CV_8UC3);
        return runtime.release();
    } catch (const std::exception& ex) { fail(ex.what()); return nullptr; }
}

extern "C" int fcv_pose_infer(void* handle, const uint8_t* bgr, int width, int height,
                               int row_stride, int max_poses, fcv_pose_detection* detections,
                               int capacity, int* count, fcv_pose_timing* timing) {
    error.clear();
    if (!handle || !bgr || !detections || !count || !timing || width <= 0 || height <= 0 ||
        row_stride < width * 3 || capacity <= 0 || max_poses <= 0 || max_poses > capacity)
        return fail("Invalid RKNN Pose inference arguments");
    *count = 0;
    *timing = {};
    auto* runtime = static_cast<Runtime*>(handle);
    const auto start = Clock::now();
    try {
        cv::Mat source(height, width, CV_8UC3, const_cast<uint8_t*>(bgr), row_stride);
        cv::cvtColor(source, runtime->rgb, cv::COLOR_BGR2RGB);
        const float scale = std::min(640.0f / width, 640.0f / height);
        int resize_w = 640;
        int resize_h = 640;
        if (640.0f / width < 640.0f / height) resize_h = static_cast<int>(height * scale);
        else resize_w = static_cast<int>(width * scale);
        if (resize_w % 4) resize_w -= resize_w % 4;
        if (resize_h % 2) resize_h -= resize_h % 2;
        if (resize_w <= 0 || resize_h <= 0) return fail("Invalid letterbox resize dimensions");
        int x_pad = 0, y_pad = 0;
        if (640.0f / width < 640.0f / height) y_pad = ((640 - resize_h) / 2) & ~1;
        else x_pad = ((640 - resize_w) / 2) & ~1;
        runtime->canvas.setTo(cv::Scalar(114, 114, 114));
        cv::resize(runtime->rgb, runtime->resized, cv::Size(resize_w, resize_h), 0, 0, cv::INTER_LINEAR);
        runtime->resized.copyTo(runtime->canvas(cv::Rect(x_pad, y_pad, resize_w, resize_h)));
        letterbox_t letterbox{x_pad, y_pad, scale};
        const auto pre_end = Clock::now();

        rknn_input input{};
        input.index = 0;
        input.type = RKNN_TENSOR_UINT8; // Official sample lets RKNN quantize the RGB bytes.
        input.fmt = RKNN_TENSOR_NHWC;
        input.size = 640 * 640 * 3;
        input.buf = runtime->canvas.data;
        int rc = rknn_inputs_set(runtime->app.rknn_ctx, 1, &input);
        if (rc != RKNN_SUCC) return fail("rknn_inputs_set failed: " + std::to_string(rc));
        rc = rknn_run(runtime->app.rknn_ctx, nullptr);
        if (rc != RKNN_SUCC) return fail("rknn_run failed: " + std::to_string(rc));
        std::array<rknn_output, 4> outputs{};
        for (int i = 0; i < 4; ++i) {
            outputs[i].index = i;
            outputs[i].want_float = 0; // INT8 detections + FP16 keypoints for official decode.
        }
        rc = rknn_outputs_get(runtime->app.rknn_ctx, 4, outputs.data(), nullptr);
        if (rc != RKNN_SUCC) return fail("rknn_outputs_get failed: " + std::to_string(rc));
        const auto infer_end = Clock::now();
        object_detect_result_list results{};
        rc = post_process(&runtime->app, outputs.data(), &letterbox,
                          BOX_THRESH, NMS_THRESH, &results);
        rknn_outputs_release(runtime->app.rknn_ctx, 4, outputs.data());
        if (rc != 0 || results.count < 0 || results.count > OBJ_NUMB_MAX_SIZE)
            return fail("Official YOLOv8 Pose decode/NMS failed");
        const int n = std::min({results.count, max_poses, capacity});
        for (int i = 0; i < n; ++i) {
            const auto& source_det = results.results[i];
            auto& out = detections[i];
            out.detector_bbox_xyxy[0] = source_det.box.left;
            out.detector_bbox_xyxy[1] = source_det.box.top;
            out.detector_bbox_xyxy[2] = source_det.box.right;
            out.detector_bbox_xyxy[3] = source_det.box.bottom;
            out.person_score = source_det.prop;
            if (!std::isfinite(out.person_score)) return fail("Nonfinite person score");
            for (int j = 0; j < 17; ++j)
                for (int k = 0; k < 3; ++k) {
                    out.keypoints[j][k] = source_det.keypoints[j][k];
                    if (!std::isfinite(out.keypoints[j][k]))
                        return fail("Nonfinite decoded keypoint");
                }
        }
        *count = n;
        const auto end = Clock::now();
        timing->preprocess_ms = elapsed_ms(start, pre_end);
        timing->inference_ms = elapsed_ms(pre_end, infer_end);
        timing->decode_ms = elapsed_ms(infer_end, end);
        timing->total_ms = elapsed_ms(start, end);
        return 0;
    } catch (const std::exception& ex) { return fail(ex.what()); }
}

extern "C" void fcv_pose_destroy(void* handle) { delete static_cast<Runtime*>(handle); }
