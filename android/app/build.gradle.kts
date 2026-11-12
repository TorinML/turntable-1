plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "io.github.torinml.turntable"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.torinml.turntable"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    // Two apps from one project: pick "mobileDebug" or "tvDebug" in Build Variants.
    flavorDimensions += "form"
    productFlavors {
        create("mobile") {
            dimension = "form"
            applicationIdSuffix = ".mobile"
        }
        create("tv") {
            dimension = "form"
            applicationIdSuffix = ".tv"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug")   // fine for sideloading onto your own devices
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    // the phone app's background player
    "mobileImplementation"("androidx.media3:media3-exoplayer:1.4.1")
    "mobileImplementation"("androidx.media3:media3-session:1.4.1")
}
