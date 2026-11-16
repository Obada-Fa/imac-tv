package com.nexus.tv

import android.Manifest
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.WindowManager
import android.widget.Toast
import java.io.DataOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import kotlin.concurrent.thread
import kotlin.math.min
import kotlin.math.roundToInt

class ScreenMirrorService : Service() {
    @Volatile private var running = false
    @Volatile private var connection: HttpURLConnection? = null
    private var projection: MediaProjection? = null
    private val main = Handler(Looper.getMainLooper())

    override fun onBind(intent: Intent?): IBinder? = null

    @Suppress("DEPRECATION")
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "stop") {
            stopSelf()
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY
        val permission = intent?.getParcelableExtra<Intent>("permission")
        val origin = intent?.getStringExtra("origin").orEmpty()
        val cookie = intent?.getStringExtra("cookie").orEmpty()
        try {
            TvConnection.validate(origin, cookie)
            requireNotNull(permission)
            val notifications = getSystemService(NotificationManager::class.java)
            notifications.createNotificationChannel(
                NotificationChannel("screen_mirror", "Screen mirroring", NotificationManager.IMPORTANCE_LOW)
            )
            val stop = PendingIntent.getService(
                this, 0, Intent(this, ScreenMirrorService::class.java).setAction("stop"),
                PendingIntent.FLAG_IMMUTABLE,
            )
            val notification = Notification.Builder(this, "screen_mirror")
                .setContentTitle("Mirroring to Nexus TV")
                .setSmallIcon(R.drawable.ic_tv_volume)
                .setOngoing(true)
                .addAction(Notification.Action.Builder(null, "Stop mirroring", stop).build())
                .build()
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(7310, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
            } else {
                startForeground(7310, notification)
            }
            val capture = getSystemService(MediaProjectionManager::class.java)
                .getMediaProjection(Activity.RESULT_OK, permission) ?: error("Screen sharing was denied")
            projection = capture
            capture.registerCallback(object : MediaProjection.Callback() {
                override fun onStop() { stopSelf() }
            }, main)
            running = true
            thread(name = "Nexus TV screen mirror") {
                try {
                    mirror(capture, origin, cookie)
                } catch (_: Exception) {
                    if (running) main.post {
                        Toast.makeText(this, "Screen sharing ended. Check the TV connection and try again.", Toast.LENGTH_LONG).show()
                    }
                } finally {
                    stopSelf()
                }
            }
        } catch (_: Exception) {
            Toast.makeText(this, "Could not start screen sharing", Toast.LENGTH_LONG).show()
            stopSelf()
        }
        return START_NOT_STICKY
    }

    @Suppress("DEPRECATION")
    private fun mirror(capture: MediaProjection, origin: String, cookie: String) {
        val metrics = android.util.DisplayMetrics()
        getSystemService(WindowManager::class.java).defaultDisplay.getRealMetrics(metrics)
        val scale = min(1.0, 1920.0 / maxOf(metrics.widthPixels, metrics.heightPixels))
        val width = ((metrics.widthPixels * scale).roundToInt() / 16 * 16).coerceAtLeast(16)
        val height = ((metrics.heightPixels * scale).roundToInt() / 16 * 16).coerceAtLeast(16)
        val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, width, height).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
            setInteger(MediaFormat.KEY_BIT_RATE, 6_000_000)
            setInteger(MediaFormat.KEY_FRAME_RATE, 30)
            if (Build.VERSION.SDK_INT >= 29) setFloat(MediaFormat.KEY_MAX_FPS_TO_ENCODER, 30f)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
        }
        val encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
        val audio = openAudio(capture)
        val http = URI(origin.trimEnd('/') + "/api/sharing/mirror").toURL().openConnection() as HttpURLConnection
        connection = http
        try {
            encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            val surface = encoder.createInputSurface()
            try {
                val display = capture.createVirtualDisplay(
                    "Nexus TV", width, height, metrics.densityDpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, surface, null, null,
                ) ?: error("Could not capture the display")
                try {
                    encoder.start()
                    http.requestMethod = "POST"
                    http.instanceFollowRedirects = false
                    http.connectTimeout = 5000
                    http.readTimeout = 5000
                    http.doOutput = true
                    http.setChunkedStreamingMode(65536)
                    http.setRequestProperty("Cookie", cookie)
                    http.setRequestProperty("Content-Type", "application/octet-stream")
                    if (audio != null) http.setRequestProperty("X-Nexus-Audio", "pcm")
                    DataOutputStream(http.outputStream).use { output ->
                        var audioThread: Thread? = null
                        try {
                            val info = MediaCodec.BufferInfo()
                            var lastSent = System.nanoTime()
                            while (running) {
                                val index = encoder.dequeueOutputBuffer(info, 100_000)
                                if (index < 0) {
                                    val now = System.nanoTime()
                                    if (now - lastSent > 3_000_000_000L) {
                                        send(output, 3, byteArrayOf(0), 0)
                                        lastSent = now
                                    }
                                    continue
                                }
                                try {
                                    if (info.size == 0) continue
                                    val buffer = encoder.getOutputBuffer(index) ?: continue
                                    val data = ByteArray(info.size)
                                    buffer.position(info.offset)
                                    buffer.get(data)
                                    val config = info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0
                                    send(output, if (config) 2 else 0, data, info.presentationTimeUs)
                                    lastSent = System.nanoTime()
                                    if (!config && audio != null && audioThread == null) {
                                        audioThread = thread(name = "Nexus TV screen audio") {
                                            sendAudio(audio, output)
                                        }
                                    }
                                } finally {
                                    encoder.releaseOutputBuffer(index, false)
                                }
                            }
                        } finally {
                            running = false
                            audioThread?.join(500)
                        }
                    }
                } finally {
                    display.release()
                }
            } finally {
                surface.release()
            }
        } finally {
            audio?.let { runCatching { it.stop() }; it.release() }
            encoder.release()
            connection = null
            http.disconnect()
        }
    }

    private fun openAudio(capture: MediaProjection): AudioRecord? {
        if (Build.VERSION.SDK_INT < 29 || checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return null
        return runCatching {
            val configuration = AudioPlaybackCaptureConfiguration.Builder(capture)
                .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
                .addMatchingUsage(AudioAttributes.USAGE_GAME)
                .build()
            val format = AudioFormat.Builder().setSampleRate(48_000)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_IN_STEREO).build()
            val record = AudioRecord.Builder().setAudioFormat(format).setBufferSizeInBytes(19_200)
                .setAudioPlaybackCaptureConfig(configuration).build()
            try { record.startRecording(); record }
            catch (err: Exception) { record.release(); throw err }
        }.getOrNull()
    }

    private fun sendAudio(record: AudioRecord, output: DataOutputStream) {
        val data = ByteArray(3840)
        try {
            while (running) {
                val count = record.read(data, 0, data.size)
                if (count <= 0) break
                val timestamp = System.nanoTime() / 1000 - count * 1_000_000L / (4 * 48_000)
                send(output, 1, data.copyOf(count), timestamp)
            }
        } catch (_: IOException) {
            stopSelf()
        }
    }

    private fun send(output: DataOutputStream, kind: Int, data: ByteArray, timestamp: Long) {
        synchronized(output) {
            output.writeByte(kind)
            output.writeInt(data.size)
            output.writeLong(timestamp.coerceAtLeast(0))
            output.write(data)
            output.flush()
        }
    }

    override fun onDestroy() {
        running = false
        projection?.stop()
        connection?.let { http -> thread { http.disconnect() } }
        super.onDestroy()
    }
}
