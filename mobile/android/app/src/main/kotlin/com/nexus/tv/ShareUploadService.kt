package com.nexus.tv

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.IBinder
import android.widget.Toast
import kotlin.concurrent.thread

/** Keeps the gallery share alive when the phone goes back to the gallery. */
class ShareUploadService : Service() {
    @Volatile private var cancelled = false
    private var worker: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "cancel") {
            cancelled = true
            worker?.interrupt()
            stopSelf()
            return START_NOT_STICKY
        }
        if (worker != null) {
            Toast.makeText(this, "A transfer is already in progress", Toast.LENGTH_SHORT).show()
            return START_NOT_STICKY
        }
        val origin = intent?.getStringExtra("origin").orEmpty()
        val cookie = intent?.getStringExtra("cookie").orEmpty()
        val clip = intent?.clipData
        val items = if (clip == null) emptyList() else (0 until clip.itemCount).map { clip.getItemAt(it).uri }
        if (items.isEmpty()) {
            stopSelf()
            return START_NOT_STICKY
        }
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel("media_upload", "TV media transfers", NotificationManager.IMPORTANCE_LOW))
        startForeground(7313, notification("Preparing media…"))
        worker = thread(name = "Nexus TV media upload") {
            try {
                PhoneMedia.upload(this, origin, cookie, items, { cancelled || Thread.currentThread().isInterrupted }) {
                    if (!cancelled) notifications.notify(7313, notification(it))
                }
            } catch (err: Exception) {
                if (!cancelled) android.os.Handler(mainLooper).post {
                    Toast.makeText(this, err.message ?: "Could not send the selected media", Toast.LENGTH_LONG).show()
                }
            } finally {
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    private fun notification(message: String): Notification {
        val cancel = PendingIntent.getService(
            this, 0, Intent(this, ShareUploadService::class.java).setAction("cancel"), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, "media_upload")
            .setContentTitle("Share to Nexus TV")
            .setContentText(message)
            .setSmallIcon(R.drawable.ic_tv_volume)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Cancel transfer", cancel).build())
            .build()
    }

    override fun onDestroy() {
        cancelled = true
        worker?.interrupt()
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    companion object {
        fun start(context: Context, origin: String, cookie: String, items: List<Uri>) {
            val clip = ClipData.newUri(context.contentResolver, "Nexus TV media", items.first())
            items.drop(1).forEach { clip.addItem(ClipData.Item(it)) }
            context.startForegroundService(Intent(context, ShareUploadService::class.java).apply {
                clipData = clip
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                putExtra("origin", origin)
                putExtra("cookie", cookie)
            })
        }
    }
}
