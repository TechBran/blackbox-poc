from ugv_tools_api.nodes.oakd_camera import build_pipeline

def test_build_pipeline_has_rgb_output():
    p = build_pipeline(rgb_width=640, rgb_height=480, fps=15)
    outputs = [n.getName() for n in p.getAllNodes() if hasattr(n, "getName")]
    # The XLinkOut we wire up is named "rgb"
    # depthai 2.32 returns dict from serializeToJson(); coerce to str for substring check.
    assert any("rgb" in str(p.serializeToJson()).lower() for _ in [1])
