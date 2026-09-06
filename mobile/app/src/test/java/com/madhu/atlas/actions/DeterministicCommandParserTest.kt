package com.madhu.atlas.actions

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DeterministicCommandParserTest {
    private val parser = DeterministicCommandParser()

    @Test fun `phone actions use OS UI and require confirmation`() {
        val call = parser.parse("call +1 312 555 0100")!!
        assertEquals(ActionKind.OPEN_DIALER.name, call.kind)
        assertEquals(ActionRisk.SENSITIVE.name, call.risk)
        assertEquals("+13125550100", call.argument)
    }

    @Test fun `contact names remain ambiguous for chat`() {
        assertNull(parser.parse("call Alice"))
    }

    @Test fun `malformed and unsafe arguments are rejected`() {
        assertNull(parser.parse("open https://"))
        assertNull(parser.parse("set alarm for 29:90"))
        assertNull(parser.parse("set a timer for 0 minutes"))
    }

    @Test fun `equivalent command gets stable idempotency key`() {
        val first = parser.parse("pause music")!!
        val second = parser.parse("pause music")!!
        assertTrue(first.actionId != second.actionId)
        assertEquals(first.idempotencyKey, second.idempotencyKey)
    }
}
