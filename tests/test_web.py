import unittest

from app import create_app


class WebUITestCase(unittest.TestCase):
    def setUp(self):
        self.client = create_app("default").test_client()

    def test_root_serves_html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.content_type)
        self.assertIn(b"Garden of Eden", resp.data)

    def test_advice_card_is_present(self):
        # The web UI is the only way most operators will reach /advice, so the
        # card and its wiring must ship with the page.
        html = self.client.get("/").data.decode()
        for needle in (
            'id="advice"',
            'id="advice-q"',
            'id="advice-send"',
            'id="advice-out"',
            'id="advice-usage"',
            'id="advice-presets"',
            "function askGroq(",
            "setupAdvice();",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)

    def test_advice_card_posts_to_the_endpoint(self):
        html = self.client.get("/").data.decode()
        # The presets must send a non-empty question, and the call must go to
        # /advice with the admin password attached when one is stored.
        self.assertIn('data-q="', html)
        self.assertIn('"/advice"', html)
        self.assertIn("headers(", html)

    def test_groq_key_is_settable_in_settings(self):
        html = self.client.get("/").data.decode()
        for needle in (
            'id="groq-key"',
            "function saveGroqKey()",
            'localStorage.setItem("groq_key"',
            "adviceHeaders(",
            'localStorage.getItem("groq_key")',
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)

    def test_advice_is_gated_on_the_admin_password(self):
        html = self.client.get("/").data.decode()
        # Only the admin password gates the control: the endpoint 401s without
        # it and no server-side setting can replace it. The Groq key must NOT
        # gate it, because the Pi can hold GROQ_API_KEY in its own .env and the
        # endpoint falls back to that -- gating on the key would grey out a
        # control that works. A missing key everywhere surfaces as a 503 whose
        # message the send path shows.
        self.assertIn("function syncAdviceGate()", html)
        self.assertIn("if (!API_KEY || !GROQ_KEY)", html)
        self.assertIn("your admin password", html)
        self.assertIn('id="advice-gate"', html)
        # The gate itself must only ever ask for the admin password.
        gate = html[html.index("function missingAdviceCreds(") :]
        gate = gate[: gate.index("function syncAdviceGate(")]
        self.assertIn("API_KEY ? []", gate)
        self.assertNotIn("GROQ_KEY", gate)

    def test_groq_key_only_attached_to_advice(self):
        html = self.client.get("/").data.decode()
        # The third-party credential must not ride along on every request.
        self.assertIn('GROQ_KEY ? { "X-Groq-Key": GROQ_KEY } : {}', html)
        # ...and the ask call must use adviceHeaders, not bare headers.
        ask = html[html.index("async function askGroq(") :]
        self.assertIn("adviceHeaders(", ask)

    def test_disabled_advice_controls_look_disabled(self):
        html = self.client.get("/").data.decode()
        # A disabled button swallows clicks with zero feedback, so every disabled
        # control has to be visibly disabled. This rule used to target only
        # button.btn, which left the Ask Groq presets full-colour with a
        # pointer cursor while being unclickable: a dead control that looked
        # alive, so clicking a preset did nothing at all.
        self.assertIn("button[disabled]", html)
        self.assertNotIn("button.btn[disabled]", html)
        # The preset hover effect must not survive the disabled state.
        self.assertIn(".presets button[disabled]:hover", html)

    def test_authenticated_media_is_fetched_not_src_assigned(self):
        # /camera/* sits behind the admin password. A bare <img>/<video> src
        # request cannot send X-API-Key, so it 401s from every browser except
        # localhost -- which is why the camera looked broken only once auth was
        # enabled, and worked fine before it. Media must go through the
        # headered fetch helper instead.
        html = self.client.get("/").data.decode()
        import re

        raw = re.findall(r'\$\("(?:cam|tl)-[a-z]+"\)\.src\s*=\s*"/camera/', html)
        self.assertEqual(raw, [], f"raw src assignment on an authenticated endpoint: {raw}")
        self.assertIn("function setAuthedMedia(", html)
        self.assertIn('setAuthedMedia($("cam-upper")', html)
        self.assertIn('setAuthedMedia($("tl-"', html)
        # Object URLs must be revoked or every refresh leaks a blob.
        self.assertIn("URL.revokeObjectURL", html)

    def test_schedule_warning_is_wired(self):
        # Cron fails silently when its target command is missing, so the UI has
        # to say so instead of showing a healthy-looking saved schedule.
        html = self.client.get("/").data.decode()
        self.assertIn('id="sched-warn"', html)
        self.assertIn("scripts_ready === false", html)
        self.assertIn(".alert-note{", html)

    def test_level_sliders_are_driven_by_the_machine(self):
        # Both sliders used to ship hardcoded placeholder labels (50% and 100%)
        # and were never written back from the API, so they showed a level the
        # hardware was not at -- a light at 30% read as 50%.
        html = self.client.get("/").data.decode()
        self.assertIn("function syncLevel(", html)
        sync = html[
            html.index("async function syncToggles(") : html.index("async function loadGrow(")
        ]
        self.assertIn('syncLevel("brightness", v)', sync)
        self.assertIn('syncLevel("speed", v)', sync)
        # Both must keep updating while the page is open, since the schedule
        # changes them unattended.
        self.assertIn("setInterval(syncToggles, 30000)", html)
        # And a placeholder must not masquerade as a real reading.
        for stale in ('id="brightness-val">50%', 'id="speed-val">100%'):
            self.assertNotIn(stale, html, f"hardcoded placeholder left in place: {stale}")

    def test_slider_sync_does_not_fight_the_user(self):
        # The 30s sync must not yank a slider out from under someone dragging it.
        html = self.client.get("/").data.decode()
        body = html[html.index("function syncLevel(") : html.index("async function syncToggles(")]
        self.assertIn("document.activeElement === el", body)

    def test_model_picker_is_in_settings(self):
        html = self.client.get("/").data.decode()
        for needle in (
            'id="set-model"',
            "function setModel(",
            "renderModelOptions",
            "/system/model",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)

    def test_pods_grid_width_follows_the_tower_count(self):
        html = self.client.get("/").data.decode()
        # The grid must be as wide as the unit has towers -- 3 across on a Home
        # line, 2 on a Studio -- rather than a hardcoded 2 that silently crops
        # a wider unit into a wrong shape.
        self.assertIn("repeat(var(--pod-cols,2),1fr)", html)
        self.assertNotIn("podwrap", html)
        # ...and the value has to actually be set from /pods at render time.
        self.assertIn('setProperty("--pod-cols", columns)', html)
        # Display order must interleave across towers per level rather than
        # reading down one tower and then the next.
        self.assertIn("function podOrder()", html)
        order = html[html.index("function podOrder()") :]
        order = order[: order.index("function renderPods(")]
        self.assertIn("col <= columns", order)
        self.assertIn("level", order)

    def test_camera_grid_width_follows_the_camera_count(self):
        html = self.client.get("/").data.decode()
        # A one-camera unit (the Studio line) must not leave half the card empty
        # beside a hidden lower camera.
        self.assertIn("repeat(var(--cam-cols,2),1fr)", html)
        self.assertIn('setProperty("--cam-cols", lowerCameraEnabled ? 2 : 1)', html)

    def test_advice_send_never_fails_silently(self):
        html = self.client.get("/").data.decode()
        # If the gate is stale -- a key cleared in another tab after the page
        # loaded -- the send path must name what is missing instead of returning
        # without a word.
        ask = html[html.index("async function askGroq(") :]
        self.assertIn("missingAdviceCreds()", ask)
        self.assertIn("in Settings first.", ask)
        # The gate hint is an instruction, not faint fine print.
        self.assertIn("#advice-gate:not(:empty)", html)


if __name__ == "__main__":
    unittest.main()
