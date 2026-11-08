package com.nexus.tv

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ResultReceiver
import android.view.KeyEvent
import android.webkit.CookieManager
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pendingStart: (() -> Unit)? = null
    private var pendingResult: MethodChannel.Result? = null
    private var pendingImage: MethodChannel.Result? = null
    private var pendingMic: ((Boolean) -> Unit)? = null
    private var linkChannel: MethodChannel? = null
    /** A nexustv:// link that started the app, until Flutter asks for it. */
    private var initialLink: String? = null

    private fun tvLink(intent: Intent?): String? =
        intent?.data?.takeIf { it.scheme == "nexustv" }?.toString()

    override fun onCreate(savedInstanceState: Bundle?) {
        // A fresh launch starts clean: sharing left over from the last run stops, and the
        // TV page turns the mic back on once it reconnects.
        if (savedInstanceState == null) {
            PhoneMicService.stop(this)
            PhoneSpeakerService.stop(this)
        }
        if (savedInstanceState == null) initialLink = tvLink(intent)
        super.onCreate(savedInstanceState)
    }

    // singleTop: a QR link while the app is open arrives here instead of a second activity.
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val link = tvLink(intent) ?: return
        setIntent(intent)
        linkChannel?.invokeMethod("link", link)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        linkChannel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nexus_tv/link").apply {
            setMethodCallHandler { call, result ->
                when (call.method) {
                    "initialLink" -> { result.success(initialLink); initialLink = null }
                    "scanQr" -> {
                        val options = GmsBarcodeScannerOptions.Builder()
                            .setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
                        GmsBarcodeScanning.getClient(this@MainActivity, options).startScan()
                            .addOnSuccessListener { result.success(it.rawValue) }
                            .addOnCanceledListener { result.success(null) }
                            .addOnFailureListener {
                                result.error("scan_failed", "Could not open the QR scanner. Google Play services is needed.", null)
                            }
                    }
                    else -> result.notImplemented()
                }
            }
        }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nexus_remote/phone_mic")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "status" -> result.success(PhoneMicService.status())
                    "stop" -> { pendingMic = null; PhoneMicService.stop(this); result.success(null) }
                    "wanted" -> result.success(getSharedPreferences("phone_mic", MODE_PRIVATE).getBoolean("wanted", true))
                    "setWanted" -> {
                        getSharedPreferences("phone_mic", MODE_PRIVATE).edit()
                            .putBoolean("wanted", call.argument<Boolean>("wanted") ?: true).apply()
                        result.success(null)
                    }
                    "start" -> {
                        val origin = call.argument<String>("origin") ?: ""
                        val cookie = call.argument<String>("cookie") ?: ""
                        try { TvConnection.validate(origin, cookie) } catch (_: Exception) {
                            result.error("invalid_connection", "Pair with the TV first", null)
                            return@setMethodCallHandler
                        }
                        val launch = { granted: Boolean ->
                          if (!granted) result.error("mic_denied", "Allow microphone access to share it with the TV.", null)
                          else {
                            try {
                                startForegroundService(Intent(this, PhoneMicService::class.java)
                                    .putExtra("origin", origin).putExtra("cookie", cookie))
                                result.success(null)
                            } catch (_: Exception) {
                                result.error("start_failed", "Could not start the phone microphone. Keep Nexus TV open and try again.", null)
                            }
                          }
                        }
                        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                            pendingMic = launch
                            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 7305)
                        } else launch(true)
                    }
                    else -> result.notImplemented()
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nexus_remote/phone_speaker")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "status" -> result.success(PhoneSpeakerService.status())
                    "stop" -> { PhoneSpeakerService.stop(this); result.success(null) }
                    "start" -> {
                        val origin = call.argument<String>("origin") ?: ""
                        val cookie = call.argument<String>("cookie") ?: ""
                        try { TvConnection.validate(origin, cookie) } catch (_: Exception) {
                            result.error("invalid_connection", "Pair with the TV first", null)
                            return@setMethodCallHandler
                        }
                        try {
                            startForegroundService(Intent(this, PhoneSpeakerService::class.java)
                                .putExtra("origin", origin).putExtra("cookie", cookie))
                            result.success(null)
                        } catch (_: Exception) {
                            result.error("start_failed", "Could not start the phone speaker. Keep Nexus TV open and try again.", null)
                        }
                    }
                    else -> result.notImplemented()
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "nexus_remote/tv_volume")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "pairCookie" -> {
                        try {
                            val origin = call.argument<String>("origin") ?: ""
                            TvConnection.validate(origin, "imactv=check")
                            val cookie = CookieManager.getInstance().getCookie(origin).orEmpty()
                                .split(';').map { it.trim() }.firstOrNull { it.startsWith("imactv=") }
                            result.success(cookie?.removePrefix("imactv=") ?: "")
                        } catch (_: Exception) { result.error("invalid_connection", "Check the TV address", null) }
                    }
                    "pickImage" -> {
                        if (pendingImage != null) result.success(emptyList<String>())
                        else {
                            pendingImage = result
                            try {
                                @Suppress("DEPRECATION")
                                startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                                    addCategory(Intent.CATEGORY_OPENABLE)
                                    type = "image/*"
                                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                }, 7303)
                            } catch (_: Exception) {
                                pendingImage = null
                                result.error("picker_unavailable", "Could not open the picture picker", null)
                            }
                        }
                    }
                    "status" -> result.success(TvVolumeService.status())
                    "stop" -> {
                        pendingStart = null
                        pendingResult?.error("cancelled", "TV volume controls were turned off", null)
                        pendingResult = null
                        TvVolumeService.stop(this)
                        result.success(null)
                    }
                    "start" -> {
                        if (pendingResult != null) {
                            result.error("busy", "TV volume controls are starting", null)
                            return@setMethodCallHandler
                        }
                        val origin = call.argument<String>("origin") ?: ""
                        val cookie = call.argument<String>("cookie") ?: ""
                        try {
                            TvConnection.validate(origin, cookie)
                        } catch (_: Exception) {
                            result.error("invalid_connection", "Pair with the TV first", null)
                            return@setMethodCallHandler
                        }
                        pendingResult = result
                        val launch = {
                            try {
                                val receiver = object : ResultReceiver(Handler(Looper.getMainLooper())) {
                                    override fun onReceiveResult(code: Int, data: Bundle?) {
                                        if (pendingResult !== result) return
                                        if (code == 0) pendingResult?.success(null)
                                        else pendingResult?.error("start_failed", data?.getString("error"), null)
                                        pendingResult = null
                                    }
                                }
                                startForegroundService(Intent(this, TvVolumeService::class.java)
                                    .putExtra("origin", origin).putExtra("cookie", cookie)
                                    .putExtra("receiver", receiver))
                            } catch (_: Exception) {
                                pendingResult?.error("start_failed", "Could not start TV controls. Keep Nexus TV open and try again.", null)
                                pendingResult = null
                            }
                            Unit
                        }
                        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                            pendingStart = launch
                            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 7301)
                        } else launch()
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 7305) {
            val launch = pendingMic
            pendingMic = null
            launch?.invoke(grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED)
            return
        }
        if (requestCode == 7301) {
            val launch = pendingStart
            pendingStart = null
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) launch?.invoke()
            else {
                pendingResult?.error("notifications_denied", "Allow notifications to keep the TV controls and Turn off button available.", null)
                pendingResult = null
            }
        }
    }

    // The activity path works on every Nexus tab. Background/locked events are
    // routed by Android's MediaSession to the same service, never to Flutter.
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (TvVolumeService.handleKey(event)) return true
        return super.dispatchKeyEvent(event)
    }

    @Suppress("DEPRECATION")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 7303) {
            val uri = data?.data?.toString()
            pendingImage?.success(if (resultCode == RESULT_OK && uri != null) listOf(uri) else emptyList<String>())
            pendingImage = null
        }
    }

    override fun onDestroy() {
        pendingImage?.success(emptyList<String>())
        pendingImage = null
        pendingResult?.error("cancelled", "TV controls were not started. Try again.", null)
        pendingResult = null
        pendingStart = null
        super.onDestroy()
    }
}
