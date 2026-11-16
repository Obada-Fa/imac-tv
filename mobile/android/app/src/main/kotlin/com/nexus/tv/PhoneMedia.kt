package com.nexus.tv

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder

internal object PhoneMedia {
    @Suppress("DEPRECATION")
    fun sharedUris(intent: Intent?): List<Uri> {
        val items = when (intent?.action) {
            Intent.ACTION_SEND -> listOfNotNull(intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))
            Intent.ACTION_SEND_MULTIPLE -> intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()
            else -> emptyList()
        }
        return items.filter { it.scheme == "content" }.distinct()
    }

    fun upload(context: Context, origin: String, cookie: String, items: List<Uri>,
               cancelled: () -> Boolean, progress: (String) -> Unit) {
        TvConnection.validate(origin, cookie)
        var session: String? = null
        val resolver = context.contentResolver
        for ((index, uri) in items.withIndex()) {
            if (cancelled()) return
            var name = "Phone media"
            var size = -1L
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
                ?.use { if (it.moveToFirst()) {
                    name = it.getString(0) ?: name
                    if (!it.isNull(1)) size = it.getLong(1)
                } }
            var staging: File? = null
            try {
                progress("Sending ${index + 1} of ${items.size}…")
                if (size < 0) {
                    staging = File.createTempFile("nexus-share-", ".upload", context.cacheDir)
                    resolver.openInputStream(uri)?.use { input ->
                        staging.outputStream().use { output -> copy(input, output, cancelled) }
                    } ?: throw IOException("Open the photo or video on your phone and share it again")
                    size = staging.length()
                }
                if (size !in 1..LIMIT) throw IOException("This file is too large or empty")
                val query = "name=" + URLEncoder.encode(name, "UTF-8") + (session?.let { "&id=$it" } ?: "")
                val http = URI(origin.trimEnd('/') + "/api/sharing/upload?" + query).toURL()
                    .openConnection() as HttpURLConnection
                try {
                    http.connectTimeout = 5000
                    http.readTimeout = 45_000
                    http.instanceFollowRedirects = false
                    http.requestMethod = "POST"
                    http.doOutput = true
                    http.setFixedLengthStreamingMode(size)
                    http.setRequestProperty("Cookie", cookie)
                    http.setRequestProperty("Content-Type", "application/octet-stream")
                    val input = staging?.inputStream() ?: resolver.openInputStream(uri)
                        ?: throw IOException("Open the file on your phone and share it again")
                    input.use { stream -> http.outputStream.use { copy(stream, it, cancelled) } }
                    val status = http.responseCode
                    val body = (if (status == 200) http.inputStream else http.errorStream)
                        ?.bufferedReader()?.use { it.readText() }.orEmpty()
                    val result = runCatching { JSONObject(body) }.getOrNull()
                    if (status != 200) throw IOException(result?.optString("error") ?: "Could not send this file")
                    session = result?.getJSONObject("result")?.getString("id")
                        ?: throw IOException("The TV did not acknowledge the upload")
                } finally {
                    http.disconnect()
                }
            } finally {
                staging?.delete()
            }
        }
        progress("Showing on TV")
    }

    private fun copy(input: InputStream, output: OutputStream, cancelled: () -> Boolean) {
        val buffer = ByteArray(65536)
        var bytes = 0L
        while (!cancelled()) {
            val count = input.read(buffer)
            if (count < 0) return
            bytes += count
            if (bytes > LIMIT) throw IOException("This file is too large to share")
            output.write(buffer, 0, count)
        }
        throw IOException("Transfer cancelled")
    }

    private const val LIMIT = 5L * 1024 * 1024 * 1024
}
