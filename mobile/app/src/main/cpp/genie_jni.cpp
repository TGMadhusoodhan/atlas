// JNI bridge from GenieEngine.kt to the Qualcomm Genie (QNN) runtime on the Hexagon NPU.
//
// STATUS: scaffold. The function signatures match the `external fun` declarations in
// GenieEngine.kt. The bodies below outline the Genie C API calls (GenieDialog_*) with
// TODOs; wire them to libGenie once the QNN SDK is on the include/link path
// (see CMakeLists.txt + docs/GENIE_SETUP.md).
//
// Genie C API reference: GenieDialogConfig_createFromJson, GenieDialog_create,
// GenieDialog_query (with a token callback), GenieDialog_free.

#include <jni.h>
#include <android/log.h>
#include <string>

// #include "GenieDialog.h"   // from the QNN SDK, once available

#define LOG_TAG "AtlasGenie"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

extern "C" {

// Returns an opaque handle (dialog pointer) or 0 on failure.
JNIEXPORT jlong JNICALL
Java_com_madhu_atlas_llm_GenieEngine_nativeInit(JNIEnv* env, jobject /*thiz*/, jstring modelDir) {
    const char* dir = env->GetStringUTFChars(modelDir, nullptr);
    LOGI("nativeInit: %s", dir);
    // TODO:
    //   std::string cfg = readFile(std::string(dir) + "/genie_config.json");
    //   GenieDialogConfig_Handle_t config;
    //   GenieDialogConfig_createFromJson(cfg.c_str(), &config);
    //   GenieDialog_Handle_t dialog;
    //   if (GenieDialog_create(config, &dialog) != GENIE_STATUS_SUCCESS) { ... return 0; }
    //   return reinterpret_cast<jlong>(dialog);
    env->ReleaseStringUTFChars(modelDir, dir);
    return 0;  // scaffold: report "not initialised" so Kotlin falls back to Echo
}

// Streams tokens by invoking callback.onToken(String); ends with onDone()/onError(String).
JNIEXPORT void JNICALL
Java_com_madhu_atlas_llm_GenieEngine_nativeGenerate(
        JNIEnv* env, jobject /*thiz*/, jlong handle, jstring prompt, jobject callback) {
    jclass cbClass = env->GetObjectClass(callback);
    jmethodID onToken = env->GetMethodID(cbClass, "onToken", "(Ljava/lang/String;)V");
    jmethodID onDone  = env->GetMethodID(cbClass, "onDone", "()V");
    jmethodID onError = env->GetMethodID(cbClass, "onError", "(Ljava/lang/String;)V");
    (void) handle; (void) prompt; (void) onToken;

    // TODO: drive GenieDialog_query with a C callback that forwards each token via
    //   env->CallVoidMethod(callback, onToken, env->NewStringUTF(tokenChunk));
    // On completion call onDone; on failure call onError with a message.
    jstring msg = env->NewStringUTF("Genie native path not built yet.");
    env->CallVoidMethod(callback, onError, msg);
    env->DeleteLocalRef(msg);
    (void) onDone;
}

JNIEXPORT void JNICALL
Java_com_madhu_atlas_llm_GenieEngine_nativeCancel(JNIEnv* /*env*/, jobject /*thiz*/, jlong handle) {
    (void) handle;
    // TODO: signal the running GenieDialog_query to abort.
}

JNIEXPORT void JNICALL
Java_com_madhu_atlas_llm_GenieEngine_nativeFree(JNIEnv* /*env*/, jobject /*thiz*/, jlong handle) {
    (void) handle;
    // TODO: GenieDialog_free(reinterpret_cast<GenieDialog_Handle_t>(handle));
}

}  // extern "C"
