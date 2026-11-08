package com.nexus.tv

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import java.net.HttpURLConnection
import java.net.URI

/** Streams the phone microphone to the TV (POST /api/mic, chunked s16le mono).
 * The TV makes it the default input while connected. Started by the user or
 * automatically after pairing; never restarts itself after process death. */
class PhoneMicService : Service() {
    companion object {
        private const val CHANNEL = "phone_mic"
        private const val NOTIFICATION = 7304
        private const val STOP = "com.nexus.tv.MIC_STOP"
        private const val RATE = 48000
        @Volatile private var instance: PhoneMicService? = null
        @Volatile private var lastError: String? = null

        fun status(): Map<String, Any?> = mapOf("enabled" to (instance?.running == true), "error" to lastError)

        fun stop(context: Context) {
            lastError = null
            instance?.shutdown()
            context.stopService(Intent(context, PhoneMicService::class.java))
        }
    }

    @Volatile private var running = false
    private var thread: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) { shutdown(); return START_NOT_STICKY }
        if (running) return START_NOT_STICKY
        val origin = intent?.getStringExtra("origin") ?: ""
        val cookie = intent?.getStringExtra("cookie") ?: ""
        try {
            TvConnection.validate(origin, cookie)
            createChannel()
            if (Build.VERSION.SDK_INT >= 30) {
                startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            } else startForeground(NOTIFICATION, notification())
        } catch (_: Exception) {
            lastError = "Could not start the phone microphone. Open Nexus TV and try again."
            stopSelf()
            return START_NOT_STICKY
        }
        running = true
        lastError = null
        instance = this
        thread = Thread({ stream(origin, cookie) }, "phone-mic").also { it.start() }
        return START_NOT_STICKY
    }

    private fun stream(origin: String, cookie: String) {
        val minimum = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val buffer = ByteArray(maxOf(minimum, 4096))
        var delay = 1000L
        while (running) {
            var record: AudioRecord? = null
            var connection: HttpURLConnection? = null
            try {
                record = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, buffer.size * 2)
                check(record.state == AudioRecord.STATE_INITIALIZED) { "Microphone is unavailable" }
                connection = URI(origin.trimEnd('/') + "/api/mic?rate=$RATE&channels=1").toURL().openConnection() as HttpURLConnection
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.connectTimeout = 4000
                connection.readTimeout = 0
                connection.instanceFollowRedirects = false
                connection.setChunkedStreamingMode(0)
                connection.setRequestProperty("Cookie", cookie)
                connection.setRequestProperty("Content-Type", "application/octet-stream")
                record.startRecording()
                connection.outputStream.use { out ->
                    delay = 1000L
                    lastError = null
                    while (running) {
                        val n = record.read(buffer, 0, buffer.size)
                        if (n < 0) throw java.io.IOException("Microphone stopped")
                        if (n > 0) out.write(buffer, 0, n)
                    }
                }
            } catch (e: Exception) {
                if (!running) break
                if (e.message?.contains("401") == true || e.message?.contains("403") == true) {
                    lastError = "Pair with the TV again to share the microphone."
                    break
                }
                lastError = "Reconnecting the phone microphone…"
            } finally {
                try { record?.stop() } catch (_: Exception) {}
                record?.release()
                connection?.disconnect()
            }
            if (!running) break
            try { Thread.sleep(delay) } catch (_: InterruptedException) { break }
            delay = minOf(delay * 2, 8000L)
        }
        if (running) { running = false; stopSelf() }
    }

    private fun createChannel() {
        getSystemService(NotificationManager::class.java)
            .createNotificationChannel(NotificationChannel(CHANNEL, "Phone microphone", NotificationManager.IMPORTANCE_LOW))
    }

    private fun notification(): Notification {
        val stop = PendingIntent.getService(this, 1,
            Intent(this, PhoneMicService::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 2,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Phone microphone is the TV's mic")
            .setContentText("Sharing your microphone with the iMac")
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Turn off", stop).build())
            .build()
    }

    internal fun shutdown() {
        running = false
        thread?.interrupt()
        if (instance === this) instance = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Swiping Nexus TV away ends sharing; nothing keeps the phone mic or speaker on in the background unasked. */
    override fun onTaskRemoved(rootIntent: Intent?) {
        shutdown()
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        running = false
        thread?.interrupt()
        if (instance === this) instance = null
        super.onDestroy()
    }
}
