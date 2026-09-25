cmake_minimum_required(VERSION 3.16)
project(fcv_rknn_pose LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(RKNN_ZOO_ROOT "$ENV{HOME}/fcv_third_party/rknn_model_zoo" CACHE PATH "Frozen RKNN Model Zoo v2.3.2 checkout")
set(RKNN_TOOLKIT_ROOT "$ENV{HOME}/fcv_third_party/rknn-toolkit2-v2.3.2" CACHE PATH "Frozen RKNN runtime SDK")
set(OFFICIAL_POSE "${RKNN_ZOO_ROOT}/examples/yolov8_pose/cpp")
set(RKNN_RUNTIME "${RKNN_TOOLKIT_ROOT}/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so")
foreach(required "${OFFICIAL_POSE}/postprocess.cc" "${RKNN_RUNTIME}")
  if(NOT EXISTS "${required}")
    message(FATAL_ERROR "Frozen upstream file missing: ${required}")
  endif()
endforeach()
find_package(OpenCV 4 REQUIRED COMPONENTS core imgproc)
add_library(fcv_rknn_pose SHARED pose_capi.cpp "${OFFICIAL_POSE}/postprocess.cc")
target_include_directories(fcv_rknn_pose PRIVATE
  "${OFFICIAL_POSE}" "${RKNN_ZOO_ROOT}/utils"
  "${RKNN_ZOO_ROOT}/3rdparty/rknpu2/include"
  "${RKNN_TOOLKIT_ROOT}/rknpu2/runtime/Linux/librknn_api/include")
target_link_libraries(fcv_rknn_pose PRIVATE ${OpenCV_LIBS} "${RKNN_RUNTIME}")
set_target_properties(fcv_rknn_pose PROPERTIES
  BUILD_RPATH "${RKNN_TOOLKIT_ROOT}/rknpu2/runtime/Linux/librknn_api/aarch64")
