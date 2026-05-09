from ugv_tools_api.schema import ToolDescriptor, ParamSchema, render_anthropic, render_openai, render_gemini

EX = ToolDescriptor(
    name="motion_move_forward",
    description="Drive straight forward for a set distance.",
    parameters={
        "distance_m": ParamSchema(type="number", minimum=0.01, maximum=2.0,
                                  description="Meters to travel forward."),
        "speed_m_s": ParamSchema(type="number", minimum=0.02, maximum=0.15, default=0.1,
                                 description="Linear velocity."),
    },
    required=["distance_m"],
)

def test_anthropic_has_input_schema():
    out = render_anthropic([EX])[0]
    assert out["name"] == "motion_move_forward"
    assert out["input_schema"]["type"] == "object"
    assert "distance_m" in out["input_schema"]["properties"]
    assert out["input_schema"]["required"] == ["distance_m"]

def test_openai_has_function_envelope():
    out = render_openai([EX])[0]
    assert out["type"] == "function"
    assert out["function"]["name"] == "motion_move_forward"
    assert "parameters" in out["function"]

def test_gemini_flat_parameters():
    out = render_gemini([EX])[0]
    assert out["name"] == "motion_move_forward"
    assert out["parameters"]["type"] == "object"

def test_params_include_default_and_bounds():
    out = render_anthropic([EX])[0]
    props = out["input_schema"]["properties"]
    assert props["distance_m"]["minimum"] == 0.01
    assert props["distance_m"]["maximum"] == 2.0
    assert "default" not in props["distance_m"]  # only on speed_m_s
    assert props["speed_m_s"]["default"] == 0.1
    assert props["speed_m_s"]["minimum"] == 0.02
