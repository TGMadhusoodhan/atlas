cmake_minimum_required(VERSION 3.22.1)
project(atlas_genie)

# JNI bridge to the Qualcomm Genie / QNN runtime (Hexagon NPU).
#
# To enable (M1 step 5):
#   1. Install the Qualcomm AI Engine Direct (QNN) SDK.
#   2. Point QNN_SDK_ROOT below (or pass -DQNN_SDK_ROOT=... from build.gradle.kts).
#   3. Uncomment the externalNativeBuild block in app/build.gradle.kts.
#
# See docs/GENIE_SETUP.md for the full procedure.

add_library(atlas_genie SHARED genie_jni.cpp)

find_library(log-lib log)

# --- Qualcomm QNN / Genie (uncomment once the SDK path is set) ---------------
# set(QNN_SDK_ROOT "" CACHE PATH "Path to the Qualcomm AI Engine Direct (QNN) SDK")
# target_include_directories(atlas_genie PRIVATE ${QNN_SDK_ROOT}/include/Genie)
# add_library(Genie SHARED IMPORTED)
# set_target_properties(Genie PROPERTIES IMPORTED_LOCATION
#     ${QNN_SDK_ROOT}/lib/aarch64-android/libGenie.so)
# target_link_libraries(atlas_genie Genie ${log-lib})

target_link_libraries(atlas_genie ${log-lib})
