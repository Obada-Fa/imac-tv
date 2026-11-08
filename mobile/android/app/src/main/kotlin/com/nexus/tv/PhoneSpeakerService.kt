package com.nexus.tv

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Build
import android.os.IBinder
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI

/** Plays the TV's phone-speaker output (GET /api/speaker, chunked s16le stereo 48 kHz)
 * on this phone. While it runs the TV makes the phone its default output, so Discord
 * and other apps are heard here. Started by the user; never restarts itself. */
class PhoneSpeakerService : Service() {
    companion object {
        private const val CHANNEL = "phone_speaker"
        private const val NOTIFICATION = 7306
        private const val STOP = "com.nexus.tv.SPEAKER_STOP"
        private const val RATE = 48000
        @Volatile private var instance: PhoneSpeakerService? = null
        @Volatile private var lastError: String? = null

        fun status(): Map<String, Any?> = mapOf("enabled" to (instance?.running == true), "error" to lastError)

        fun stop(context: Context) {
            lastError = null
            instance?.shutdown()
            context.stopService(Intent(context, PhoneSpeakerService::class.java))
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
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
            } else startForeground(NOTIFICATION, notification())
        } catch (_: Exception) {
            lastError = "Could not start the phone speaker. Open Nexus TV and try again."
            stopSelf()
            return START_NOT_STICKY
        }
        running = true
        lastError = null
        instance = this
        thread = Thread({ stream(origin, cookie) }, "phone-speaker").also { it.start() }
        return START_NOT_STICKY
    }

    /** Voice-call audio goes to the quiet earpiece by default. Use a headset when one is
     * connected, otherwise the loudspeaker; call mode keeps the phone mic's echo cancelling on. */
    @Suppress("DEPRECATION")
    private fun routeAudio(audio: AudioManager) {
        audio.mode = AudioManager.MODE_IN_COMMUNICATION
        if (Build.VERSION.SDK_INT >= 31) {
            val devices = audio.availableCommunicationDevices
            val headset = listOf(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET,
                AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_USB_HEADSET)
            val pick = headset.firstNotNullOfOrNull { type -> devices.firstOrNull { it.type == type } }
                ?: devices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
            if (pick != null) audio.setCommunicationDevice(pick)
        } else {
            audio.isSpeakerphoneOn = !audio.isWiredHeadsetOn && !audio.isBluetoothScoOn
        }
    }

    @Suppress("DEPRECATION")
    private fun releaseAudio(audio: AudioManager) {
        try {
            if (Build.VERSION.SDK_INT >= 31) audio.clearCommunicationDevice()
            else audio.isSpeakerphoneOn = false
            audio.mode = AudioManager.MODE_NORMAL
        } catch (_: Exception) {}
    }

    private fun stream(origin: String, cookie: String) {
        val audio = getSystemService(AudioManager::class.java)
        try { routeAudio(audio) } catch (_: Exception) {}
        try { streamLoop(origin, cookie) } finally { releaseAudio(audio) }
    }

    private fun streamLoop(origin: String, cookie: String) {
        val minimum = AudioTrack.getMinBufferSize(RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT)
        val buffer = ByteArray(8192)
        var delay = 1000L
        while (running) {
            var track: AudioTrack? = null
            var connection: HttpURLConnection? = null
            try {
                track = AudioTrack.Builder()
                    .setAudioAttributes(AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(RATE)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).build())
                    .setBufferSizeInBytes(maxOf(minimum, 16384))
                    .setTransferMode(AudioTrack.MODE_STREAM).build()
                connection = URI(origin.trimEnd('/') + "/api/speaker").toURL().openConnection() as HttpURLConnection
                connection.connectTimeout = 4000
                connection.readTimeout = 8000
                connection.instanceFollowRedirects = false
                connection.setRequestProperty("Cookie", cookie)
                val status = connection.responseCode
                if (status == 401 || status == 403) throw TvAuthenticationException()
                if (status != 200) throw IOException("TV returned $status")
                track.play()
                connection.inputStream.use { input ->
                    delay = 1000L
                    lastError = null
                    while (running) {
                        val n = input.read(buffer)
                        if (n < 0) throw IOException("Speaker stream ended")
                        if (n > 0) track.write(buffer, 0, n)
                    }
                }
            } catch (e: TvAuthenticationException) {
                lastError = "Pair with the TV again to use the phone speaker."
                break
            } catch (e: Exception) {
                if (!running) break
                lastError = "Reconnecting the phone speaker…"
            } finally {
                try { track?.stop() } catch (_: Exception) {}
                track?.release()
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
            .createNotificationChannel(NotificationChannel(CHANNEL, "Phone speaker", NotificationManager.IMPORTANCE_LOW))
    }

    private fun notification(): Notification {
        val stop = PendingIntent.getService(this, 3,
            Intent(this, PhoneSpeakerService::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 4,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_silent_mode_off)
            .setContentTitle("Phone speaker is the TV's output")
            .setContentText("Playing the iMac's audio on this phone")
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
