# ONNX Runtime
-keep class ai.onnxruntime.** { *; }
# ObjectBox
-keep class io.objectbox.** { *; }
-keep @io.objectbox.annotation.Entity class * { *; }
# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**
-keepclassmembers class **$$serializer { *; }
-keep,includedescriptorclasses class com.madhu.atlas.**$$serializer { *; }
-keepclassmembers class com.madhu.atlas.** {
    *** Companion;
}
