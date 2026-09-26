#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FCV_POSE_ABI_VERSION 1

typedef struct {
    float detector_bbox_xyxy[4];
    float person_score;
    float keypoints[17][3];
} fcv_pose_detection;

typedef struct {
    double preprocess_ms;
    double inference_ms;
    double decode_ms;
    double total_ms;
} fcv_pose_timing;

int fcv_pose_abi_version(void);
void* fcv_pose_create(const char* model_path);
/* Optional scheduling experiment; 0 keeps the existing RKNN AUTO policy. */
void* fcv_pose_create_with_core_mask(const char* model_path, int core_mask);
int fcv_pose_infer(void* handle, const uint8_t* bgr, int width, int height,
                   int row_stride, int max_poses, fcv_pose_detection* detections,
                   int capacity, int* count, fcv_pose_timing* timing);
void fcv_pose_destroy(void* handle);
const char* fcv_pose_last_error(void);

#ifdef __cplusplus
}
#endif
