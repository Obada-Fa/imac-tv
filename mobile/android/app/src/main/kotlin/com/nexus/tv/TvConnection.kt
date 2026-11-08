package com.nexus.tv

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI

internal class TvAuthenticationException : IOException("Pair with the TV again")

/** The same cookie-authenticated API as the browser remote. No redirects or retries:
 * replaying a relative volume command after a timeout could change volume twice. */
internal class TvConnection(private val origin: String, private val cookie: String) {
    init { validate(origin, cookie) }

    companion object {
        fun validate(origin: String, cookie: String) {
            val uri = URI(origin)
            require(uri.scheme in listOf("http", "https") && !uri.host.isNullOrBlank())
            require(uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null)
            require(uri.path.isNullOrEmpty() || uri.path == "/")
            require(cookie.matches(Regex("imactv=[A-Za-z0-9_=-]+")))
        }
    }

    fun state(): JSONObject = request("/api/state")
    fun command(action: String, params: JSONObject = JSONObject()): JSONObject =
        request("/api/commands", JSONObject().put("action", action).put("params", params))

    private fun request(path: String, body: JSONObject? = null): JSONObject {
        val connection = URI(origin.trimEnd('/') + path).toURL().openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 2500
            connection.readTimeout = 2500
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Cookie", cookie)
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val status = connection.responseCode
            if (status == 401 || status == 403) throw TvAuthenticationException()
            if (status != 200) throw IOException("TV returned $status")
            val text = connection.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(text)
            if (body != null && !json.optBoolean("ok", false)) throw IOException("TV rejected command")
            return json
        } finally {
            connection.disconnect()
        }
    }
}
