"""Offline tests for m2g.error_classify (Phase 3): classification of dependency, preflight, startup, model-loading, inference,
network, file-transfer and Colab runtime errors, and the bounded-retry policy. Pure functions, no Colab, no network."""
import unittest

from m2g import error_classify as ec


class TestClassifyText(unittest.TestCase):
    def check(self, text, category, code, transient, stage=""):
        info = ec.classify_text(text, stage)
        self.assertEqual((info.category, info.code, info.transient), (category, code, transient), info)
        if not transient:
            self.assertEqual(info.max_retries, 0)
        return info

    def test_dependency_errors(self):
        self.check("ModuleNotFoundError: No module named 'timm'", "dependency", "missing_module", False)
        self.check("ERROR: No matching distribution found for nope==9", "dependency", "pip_unsatisfiable", False)
        self.check("ERROR: Could not find a version that satisfies the requirement x", "dependency", "pip_unsatisfiable", False)
        self.check("ERROR: ResolutionImpossible: for help visit ...", "dependency", "pip_unsatisfiable", False)

    def test_model_loading_errors(self):
        self.check("OSError: You are trying to access a gated repo.", "model_loading", "model_access_denied", False)
        self.check("requests.exceptions.HTTPError: 401 Client Error: Unauthorized", "model_loading", "model_access_denied", False)
        self.check("huggingface_hub.errors.RepositoryNotFoundError: Repository Not Found for url", "model_loading",
                   "model_not_found", False)
        self.check("ValueError: The checkpoint you are trying to load has model type `foo` but Transformers does not recognize "
                   "this architecture.", "model_loading", "unsupported_architecture", False)
        self.check("safetensors_rust.SafetensorError: Error while deserializing header: MetadataIncompleteBuffer",
                   "model_loading", "partial_download", True)
        self.check("ValueError: Loading X requires you to execute the configuration file: set trust_remote_code=True",
                   "model_loading", "needs_remote_code", False)

    def test_resource_errors(self):
        self.check("torch.cuda.OutOfMemoryError: CUDA out of memory. Tried to allocate 2 GiB", "resource", "gpu_oom", False)
        self.check("OSError: [Errno 28] No space left on device", "resource", "disk_full", False)

    def test_network_errors_are_transient_but_bounded(self):
        for text in ("requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='huggingface.co'): Read timed out.",
                     "WARNING: Retrying (Retry(total=4)) after connection broken by ConnectionResetError",
                     "urllib3.exceptions.MaxRetryError: HTTPSConnectionPool: Max retries exceeded with url",
                     "socket.gaierror: Temporary failure in name resolution",
                     "requests.exceptions.HTTPError: 503 Server Error: Service Unavailable",
                     "429 Client Error: Too Many Requests for url"):
            info = self.check(text, "network", "transient_network", True)
            self.assertTrue(0 < info.max_retries <= ec.MAX_RETRIES_CEILING)

    def test_a_401_is_never_called_network(self):
        info = ec.classify_text("401 Client Error: Unauthorized ... Max retries exceeded")
        self.assertEqual(info.code, "model_access_denied")
        self.assertFalse(info.transient)

    def test_share_tunnel_is_not_retried_again(self):
        info = self.check("Could not create share link. Connection refused", "network", "share_tunnel", False)
        self.assertIn("status.gradio.app", info.recovery)

    def test_runtime_and_auth_errors(self):
        self.check("RuntimeError: Connection was lost.", "colab_runtime", "kernel_connection", True)
        self.check("RefreshError: invalid_grant: Token has been expired or revoked", "auth", "google_auth", False)
        self.check("usage limit reached for this account", "colab_runtime", "quota", False)

    def test_inference_errors(self):
        self.check("RuntimeError: Expected all tensors to be on the same device", "inference", "bad_input_or_shape", False)
        info = self.check("something nobody has seen before", "inference", "unrecognized", False, stage="inference")
        self.assertIn("diagnostics", info.recovery)

    def test_unknown_errors_are_permanent_and_take_the_stage_category(self):
        for stage, category in (("install", "dependency"), ("preflight", "preflight"), ("start", "startup"),
                                ("transfer", "file_transfer"), ("runtime", "colab_runtime"), ("", "unknown")):
            info = ec.classify_text("a brand new failure", stage)
            self.assertEqual((info.category, info.transient, info.max_retries), (category, False, 0))

    def test_api_incompatibility(self):
        self.check("TypeError: ChatInterface.__init__() got an unexpected keyword argument 'type'", "startup", "api_incompatible",
                   False)

    def test_technical_line_is_the_exception_not_the_frame(self):
        text = 'Traceback (most recent call last):\n  File "app.py", line 3, in <module>\n    x()\nKeyError: \'boom\'\n'
        self.assertEqual(ec.error_line(text), "KeyError: 'boom'")
        self.assertEqual(ec.classify_text(text).technical, "KeyError: 'boom'")


class TestClassifyFailure(unittest.TestCase):
    def test_definitive_kinds_win_over_text(self):
        info = ec.classify_failure("transfer_failed", "Connection reset while writing", "ReadTimeout")
        self.assertEqual((info.category, info.transient), ("file_transfer", False))
        info = ec.classify_failure("preflight_failed", "Preflight blocked", "ModuleNotFoundError: No module named 'gradio'")
        self.assertEqual((info.category, info.code), ("preflight", "gradio_not_importable"))
        self.assertEqual(ec.classify_failure("cancelled", "Cancelled").category, "cancelled")
        self.assertEqual(ec.classify_failure("needs_auth", "x").category, "auth")

    def test_text_refines_generic_kinds(self):
        info = ec.classify_failure("install_failed", "pip failed", "ERROR: No matching distribution found for x", "install")
        self.assertEqual((info.category, info.code), ("dependency", "pip_unsatisfiable"))
        info = ec.classify_failure("app_failed", "The app failed", "OSError: gated repo", "start")
        self.assertEqual((info.category, info.code), ("model_loading", "model_access_denied"))

    def test_a_network_error_at_start_up_is_a_model_download_problem(self):
        info = ec.classify_failure("app_failed", "x", "requests.exceptions.ReadTimeout: Read timed out", "start")
        self.assertEqual((info.category, info.code, info.transient), ("model_loading", "download_network_error", True))

    def test_unrecognised_text_falls_back_to_the_kind_default_and_is_permanent(self):
        for kind, category in (("install_failed", "dependency"), ("app_failed", "startup"), ("start_failed", "startup"),
                               ("create_failed", "colab_runtime")):
            info = ec.classify_failure(kind, "mystery", "mystery")
            self.assertEqual((info.category, info.transient, info.max_retries), (category, False, 0), kind)

    def test_every_category_used_is_declared(self):
        kinds = list(ec._KIND_TABLE) + list(ec._KIND_DEFAULT)
        for k in kinds:
            self.assertIn(ec.classify_failure(k, "m", "m").category, ec.CATEGORIES)
        for rule in ec._RULES:
            self.assertIn(rule[1], ec.CATEGORIES)


class TestRetryPolicy(unittest.TestCase):
    def test_permanent_errors_never_retry(self):
        info = ec.classify_text("No matching distribution found")
        for attempts in range(1, 6):
            self.assertFalse(ec.may_retry(info, attempts, 99))

    def test_transient_retries_are_bounded_by_rule_caller_and_ceiling(self):
        net = ec.classify_text("Read timed out")
        self.assertEqual(net.max_retries, 2)
        self.assertEqual([ec.may_retry(net, a, 5) for a in range(1, 5)], [True, True, False, False])  # rule bound 2
        self.assertEqual([ec.may_retry(net, a, 1) for a in range(1, 4)], [True, False, False])  # caller bound 1
        self.assertFalse(ec.may_retry(net, 1, 0))  # caller bound 0 = no retries
        self.assertLessEqual(max(r[4] for r in ec._RULES), ec.MAX_RETRIES_CEILING)

    def test_unknown_errors_are_never_retried(self):
        self.assertFalse(ec.may_retry(ec.classify_text("???"), 1, 5))

    def test_delay_grows_and_is_capped(self):
        self.assertEqual([ec.retry_delay(a, 8, 20) for a in (1, 2, 3, 4)], [8, 16, 20, 20])
        self.assertEqual(ec.retry_delay(0, 8), 8)

    def test_user_text_names_the_cause_and_the_next_step(self):
        text = ec.explain("CUDA out of memory")
        self.assertIn("resource/gpu_oom", text)
        self.assertIn("What to do", text)


if __name__ == "__main__":
    unittest.main()
