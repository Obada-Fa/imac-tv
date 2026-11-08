package com.nexus.tv

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioManager
import android.media.MediaMetadata
import android.media.VolumeProvider
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.ResultReceiver
import android.os.SystemClock
import android.view.KeyEvent
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** A user-started network-device session. It survives Flutter being suspended,
 * but never restarts itself after process death, force-stop, or phone reboot. */
class TvVolumeService : Service() {
    companion object {
        private const val CHANNEL = "tv_volume"
        private const val NOTIFICATION = 7302
        private const val STOP = "com.nexus.tv.TV_STOP"
        private const val PLAY = "com.nexus.tv.TV_PLAY"
        @Volatile private var instance: TvVolumeService? = null
        @Volatile private var lastError: String? = null

        fun status(): Map<String, Any?> = mapOf("enabled" to (instance?.running == true), "error" to lastError)

        fun stop(context: Context) {
            lastError = null
            instance?.shutdown()
            context.stopService(Intent(context, TvVolumeService::class.java))
        }

        fun handleKey(event: KeyEvent): Boolean {
            val service = instance?.takeIf { it.running } ?: return false
            val direction = when (event.keyCode) {
                KeyEvent.KEYCODE_VOLUME_UP -> 1
                KeyEvent.KEYCODE_VOLUME_DOWN -> -1
                KeyEvent.KEYCODE_VOLUME_MUTE -> 0
                else -> return false
            }
            if (event.action == KeyEvent.ACTION_DOWN &&
                (direction != 0 || event.repeatCount == 0)) {
                service.adjust(direction)
            }
            return true
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadScheduledExecutor()
    private val pendingCommands = AtomicInteger()
    @Volatile private var running = false
    private lateinit var connection: TvConnection
    private lateinit var session: MediaSession
    private lateinit var provider: VolumeProvider
    private var failures = 0
    private var title = "iMac TV"
    private var paused = true
    private var mediaState = JSONObject()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            STOP -> { shutdown(); return START_NOT_STICKY }
            PLAY -> { if (running) mediaAction("toggle"); else stopSelf(); return START_NOT_STICKY }
        }
        @Suppress("DEPRECATION")
        val receiver = intent?.getParcelableExtra<ResultReceiver>("receiver")
        if (running) {
            receiver?.send(0, Bundle())
            return START_NOT_STICKY
        }
        try {
            connection = TvConnection(intent?.getStringExtra("origin") ?: "", intent?.getStringExtra("cookie") ?: "")
            session = MediaSession(this, "Nexus TV")
            provider = object : VolumeProvider(VOLUME_CONTROL_RELATIVE, 100, 50) {
                override fun onAdjustVolume(direction: Int) {
                    // ADJUST_SAME must not become a mute command.
                    when (direction) {
                        AudioManager.ADJUST_RAISE -> adjust(1)
                        AudioManager.ADJUST_LOWER -> adjust(-1)
                        AudioManager.ADJUST_TOGGLE_MUTE -> adjust(0)
                    }
                }
            }
            session.setPlaybackToRemote(provider)
            session.setCallback(object : MediaSession.Callback() {
                override fun onPlay() { mediaAction("play") }
                override fun onPause() { mediaAction("pause") }
                override fun onStop() { mediaAction("stop") }
                override fun onSeekTo(pos: Long) { mediaAction("seek", pos / 1000.0) }
                override fun onFastForward() { mediaAction("forward") }
                override fun onRewind() { mediaAction("backward") }
                override fun onSkipToNext() { mediaAction("next") }
                override fun onSkipToPrevious() { mediaAction("previous") }
            }, main)
            session.setSessionActivity(openApp())
            @Suppress("DEPRECATION")
            session.setFlags(MediaSession.FLAG_HANDLES_MEDIA_BUTTONS or MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS)
            publishPlayback()
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL, "TV remote controls", NotificationManager.IMPORTANCE_LOW))
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            } else startForeground(NOTIFICATION, notification())
            running = true
            lastError = null
            instance = this
            session.isActive = true
            worker.scheduleWithFixedDelay({ poll() }, 0, 5, TimeUnit.SECONDS)
            receiver?.send(0, Bundle())
        } catch (_: Exception) {
            val message = "Could not start TV controls. Open Nexus TV and try again."
            receiver?.send(1, Bundle().apply { putString("error", message) })
            shutdown(message)
        }
        return START_NOT_STICKY
    }

    internal fun adjust(direction: Int) {
        if (!running) return
        if (direction == 0) command("mute")
        else command("volume", JSONObject().put("direction", if (direction > 0) 1 else -1))
    }

    private fun command(action: String, params: JSONObject = JSONObject()) = enqueue { connection.command(action, params) }

    // Read fresh TV state before selecting a source or seeking. Play and Pause
    // are idempotent, so another remote cannot turn a stale pause into a play.
    internal fun mediaAction(action: String, position: Double = 0.0) = enqueue {
        val state = connection.state()
        val pb = state.optJSONObject("playback") ?: JSONObject()
        val yt = state.optJSONObject("youtube_now") ?: JSONObject()
        val player = pb.optBoolean("active")
        if (!player && yt.optString("status") !in listOf("Playing", "Paused")) return@enqueue JSONObject()
        val duration = if (player) pb.optDouble("duration", 0.0) else yt.optDouble("length", 0.0)
        val current = if (player) pb.optDouble("position", 0.0) else yt.optDouble("position", 0.0)
        var command = ""
        val params = JSONObject()
        when (action) {
            "play", "pause", "toggle" -> {
                if (!player && !yt.optBoolean("can_pause", true)) return@enqueue JSONObject()
                val pause = if (action == "toggle") !(if (player) pb.optBoolean("paused") else yt.optString("status") == "Paused") else action == "pause"
                command = if (player) "set_paused" else "youtube_media"
                if (player) params.put("paused", pause) else params.put("cmd", if (pause) "pause" else "play")
            }
            "stop" -> { command = if (player) "back" else "youtube_media"; if (!player) params.put("cmd", "stop") }
            "next", "previous" -> {
                if (player && (action != "next" || state.optJSONObject("media")?.optJSONObject("next") == null)) return@enqueue JSONObject()
                if (!player && !yt.optBoolean(if (action == "next") "can_next" else "can_prev", true)) return@enqueue JSONObject()
                command = if (player) "next_episode" else "youtube_media"
                if (!player) params.put("cmd", action)
            }
            "seek", "forward", "backward" -> {
                if (duration <= 0 || (!player && !yt.optBoolean("can_seek", true))) return@enqueue JSONObject()
                val seconds = (if (action == "seek") position else current + if (action == "forward") 10.0 else -10.0)
                    .coerceIn(0.0, minOf(duration, 86400.0))
                command = if (player) "seek" else "youtube_media"
                if (player) params.put("seconds", seconds) else params.put("cmd", "set_position").put("position", seconds)
            }
            else -> return@enqueue JSONObject()
        }
        connection.command(command, params)
    }

    private fun enqueue(request: () -> JSONObject) {
        if (!running) return
        // A held button on a slow/offline LAN must not build a long queue of
        // volume changes that execute several seconds after it is released.
        if (pendingCommands.incrementAndGet() > 8) {
            pendingCommands.decrementAndGet()
            return
        }
        val queuedAt = SystemClock.elapsedRealtime()
        worker.execute {
            try {
                if (!running || SystemClock.elapsedRealtime() - queuedAt > 1500) return@execute
                withWakeLock {
                    val response = request()
                    val volume = response.optJSONObject("result")?.optInt("volume", -1) ?: -1
                    if (volume in 0..100) main.post { if (running) provider.setCurrentVolume(volume) }
                    failures = 0
                    poll()
                }
            } catch (e: Exception) {
                failed(e)
            } finally {
                pendingCommands.decrementAndGet()
            }
        }
    }

    private fun poll() {
        if (!running) return
        try {
            withWakeLock {
                val state = connection.state()
                val volume = state.optInt("volume", -1)
                if (volume !in 0..100) throw java.io.IOException("TV volume unavailable")
                val playback = state.optJSONObject("playback")
                val youtube = state.optJSONObject("youtube_now")
                val playerActive = playback?.optBoolean("active") == true
                val isPlaying = if (playerActive) !playback!!.optBoolean("paused") else youtube?.optString("status") == "Playing"
                val mediaTitle = (if (playerActive) state.optJSONObject("media")?.optString("name").orEmpty()
                    .ifBlank { playback?.optString("title").orEmpty() } else youtube?.optString("title").orEmpty())
                    .ifBlank { "iMac TV" }
                failures = 0
                main.post {
                    if (!running) return@post
                    provider.setCurrentVolume(volume)
                    paused = !isPlaying
                    title = mediaTitle
                    mediaState = state
                    publishPlayback()
                    getSystemService(NotificationManager::class.java).notify(NOTIFICATION, notification())
                }
            }
        } catch (e: Exception) { failed(e) }
    }

    private fun failed(error: Exception) {
        if (!running) return
        if (error is TvAuthenticationException) {
            main.post { shutdown("Pair with the TV again to enable volume buttons.") }
        } else if (++failures >= 3) {
            main.post { shutdown("TV disconnected. Reconnect, then enable TV volume buttons again.") }
        }
    }

    private fun withWakeLock(block: () -> Unit) {
        val lock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$packageName:tv-command")
        lock.acquire(15000)
        try { block() } finally { if (lock.isHeld) lock.release() }
    }

    private fun publishPlayback() {
        val pb = mediaState.optJSONObject("playback") ?: JSONObject()
        val yt = mediaState.optJSONObject("youtube_now") ?: JSONObject()
        val player = pb.optBoolean("active")
        val youtube = !player && yt.optString("status") in listOf("Playing", "Paused")
        val duration = if (player) pb.optDouble("duration", 0.0) else yt.optDouble("length", 0.0)
        val position = if (player) pb.optDouble("position", 0.0) else yt.optDouble("position", 0.0)
        var actions = 0L
        if (player || youtube) {
            actions = PlaybackState.ACTION_STOP
            if (player || yt.optBoolean("can_pause", true)) actions = actions or PlaybackState.ACTION_PLAY or PlaybackState.ACTION_PAUSE or PlaybackState.ACTION_PLAY_PAUSE
            if (duration > 0 && (player || yt.optBoolean("can_seek", true))) actions = actions or PlaybackState.ACTION_SEEK_TO or PlaybackState.ACTION_FAST_FORWARD or PlaybackState.ACTION_REWIND
            if ((player && mediaState.optJSONObject("media")?.optJSONObject("next") != null) || (youtube && yt.optBoolean("can_next", true))) actions = actions or PlaybackState.ACTION_SKIP_TO_NEXT
            if (youtube && yt.optBoolean("can_prev", true)) actions = actions or PlaybackState.ACTION_SKIP_TO_PREVIOUS
        }
        session.setMetadata(MediaMetadata.Builder()
            .putString(MediaMetadata.METADATA_KEY_TITLE, title)
            .putLong(MediaMetadata.METADATA_KEY_DURATION, (duration.coerceAtLeast(0.0) * 1000).toLong())
            .putString(MediaMetadata.METADATA_KEY_ARTIST, "Nexus TV").build())
        session.setPlaybackState(PlaybackState.Builder()
            .setActions(actions)
            .setState(if (paused) PlaybackState.STATE_PAUSED else PlaybackState.STATE_PLAYING,
                (position.coerceAtLeast(0.0) * 1000).toLong(), if (paused) 0f else 1f).build())
    }

    private fun openApp(): PendingIntent = PendingIntent.getActivity(this, 0,
        Intent(this, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun serviceAction(action: String, code: Int): PendingIntent = PendingIntent.getService(this, code,
        Intent(this, TvVolumeService::class.java).setAction(action),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    private fun notification(): Notification = Notification.Builder(this, CHANNEL)
        .setSmallIcon(R.drawable.ic_tv_volume)
        .setContentTitle(title)
        .setContentText("Phone volume buttons control iMac TV")
        .setContentIntent(openApp())
        .setDeleteIntent(serviceAction(STOP, 2))
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .setVisibility(Notification.VISIBILITY_PUBLIC)
        .addAction(Notification.Action.Builder(
            if (paused) android.R.drawable.ic_media_play else android.R.drawable.ic_media_pause,
            if (paused) "Play" else "Pause", serviceAction(PLAY, 1)).build())
        .addAction(Notification.Action.Builder(android.R.drawable.ic_menu_close_clear_cancel,
            "Turn off", serviceAction(STOP, 2)).build())
        .setStyle(Notification.MediaStyle().setMediaSession(session.sessionToken).setShowActionsInCompactView(0, 1))
        .build()

    private fun shutdown(reason: String? = null) {
        running = false
        lastError = reason
        if (instance === this) instance = null
        if (::session.isInitialized) { session.isActive = false; session.release() }
        worker.shutdownNow()
        main.removeCallbacksAndMessages(null)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        shutdown(lastError)
        super.onDestroy()
    }
}
