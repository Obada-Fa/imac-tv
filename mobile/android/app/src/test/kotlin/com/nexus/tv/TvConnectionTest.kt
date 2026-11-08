package com.nexus.tv

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class TvConnectionTest {
    private lateinit var server: MockWebServer
    private lateinit var connection: TvConnection
    @Before fun setup() {
        server = MockWebServer()
        server.start()
        connection = TvConnection(server.url("/").toString(), "imactv=paired-token")
    }
    @After fun cleanup() { server.shutdown() }

    @Test fun volumeUsesAuthenticatedRelativeCommand() {
        server.enqueue(MockResponse().setBody("""{"ok":true,"result":{"volume":45}}"""))
        assertEquals(45, connection.command("volume", JSONObject().put("direction", -1)).getJSONObject("result").getInt("volume"))
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/commands", request.path)
        assertEquals("imactv=paired-token", request.getHeader("Cookie"))
        val body = JSONObject(request.body.readUtf8())
        assertEquals("volume", body.getString("action"))
        assertEquals(-1, body.getJSONObject("params").getInt("direction"))
    }

    @Test fun revokedPairingIsAnAuthenticationFailure() {
        server.enqueue(MockResponse().setResponseCode(403))
        assertThrows(TvAuthenticationException::class.java) { connection.state() }
    }

    @Test fun refusesRedirectWithoutLeakingCookieOrReplayingCommand() {
        server.enqueue(MockResponse().setResponseCode(307).addHeader("Location", server.url("/redirect")))
        server.enqueue(MockResponse().setBody("""{"ok":true}"""))
        assertThrows(java.io.IOException::class.java) { connection.command("volume", JSONObject().put("direction", 1)) }
        assertEquals(1, server.requestCount)
    }

    @Test fun rejectsFalseSuccessAndInvalidCredentials() {
        server.enqueue(MockResponse().setBody("""{"ok":false}"""))
        assertThrows(java.io.IOException::class.java) { connection.command("mute") }
        assertThrows(IllegalArgumentException::class.java) { TvConnection.validate("http://tv.test?key=secret", "imactv=token") }
        assertThrows(IllegalArgumentException::class.java) { TvConnection.validate("http://tv.test", "imactv=token\r\nInjected: yes") }
    }
}
