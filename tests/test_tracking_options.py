from pathlib import Path

from app.options_dialog import CPU_OPTION, tracking_option_specs
from app.runner import PipelineRunner


def _config():
    return {
        "models": {
            "env": {"weights_dir": Path("a"), "instances": [], "batch": 32},
            "mouse": {"weights_dir": Path("b"), "instances": [], "batch": 8},
        }
    }


def _runner(options):
    return PipelineRunner(_config(), {}, Path("out"), [], skip_tracking=True, options=options)


def test_batch_defaults_to_pipeline_value():
    args = _runner({})._tracking_model_args()
    assert [a["batch"] for a in args] == [32, 8]


def test_batch_override_is_per_model():
    args = _runner({"batch:mouse": 2})._tracking_model_args()
    assert [a["batch"] for a in args] == [32, 2]


def test_specs_show_each_models_default():
    specs = tracking_option_specs(_config())
    batch_rows = [s for s in specs if s["name"].startswith("batch:")]
    assert [(s["label"], s["default"]) for s in batch_rows] == [
        ("Batch size (env)", 32),
        ("Batch size (mouse)", 8),
    ]
    assert specs[-1]["name"] == CPU_OPTION


def test_single_model_label_has_no_role():
    cfg = {"models": {"only": {"weights_dir": Path("a"), "instances": [], "batch": 4}}}
    assert tracking_option_specs(cfg)[0]["label"] == "Batch size"


def _logged(options, config=None):
    runner = PipelineRunner(
        config or _config(), {}, Path("out"), [], skip_tracking=True, options=options
    )
    lines: list[str] = []
    runner.log.connect(lines.append)
    runner._log_batch_sizes(runner._tracking_model_args())
    return lines


def test_log_lists_batch_size_per_model_with_hint():
    lines = _logged({"batch:mouse": 4})
    assert lines[0] == "Tracking batch size: env 32, mouse 4."
    assert "lowering the batch size in Advanced Options" in lines[1]


def test_log_single_model_has_no_role_name():
    cfg = {"models": {"only": {"weights_dir": Path("a"), "instances": [], "batch": 6}}}
    assert _logged({}, cfg)[0] == "Tracking batch size: 6."
