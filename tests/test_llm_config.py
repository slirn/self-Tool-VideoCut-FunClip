"""llm_config（多厂商模型注册表）单元测试 — REQ-20260915-008 v2。"""

from __future__ import annotations

import json

import pytest

from slirn_home import llm_config


@pytest.fixture()
def root(tmp_path):
    return tmp_path  # config/llm.json 不存在 → 默认注册表


DEEPSEEK = {
    "id": "deepseek-chat",
    "provider": "DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key_env": "DEEPSEEK_API_KEY",
}


def _add(root, **kw):
    return llm_config.add_model(
        root, kw["id"], kw["provider"], kw["base_url"], kw["api_key_env"],
        kw.get("protocol", "openai"),
    )


# ---------- 默认与回退 ----------

def test_defaults_when_no_config(root):
    models = llm_config.list_models(root)
    assert [m["id"] for m in models] == ["qwen-plus"]
    assert models[0]["api_key_env"] == "DASHSCOPE_API_KEY"
    assert llm_config.get_current(root) == "qwen-plus"


def test_list_models_key_present(root, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    models = llm_config.list_models(root)
    assert models[0]["key_present"] is True
    monkeypatch.delenv("DASHSCOPE_API_KEY")
    assert llm_config.list_models(root)[0]["key_present"] is False


def test_legacy_single_model_format_ignored(root):
    """旧版 {"model": x} 格式 → 忽略，回退默认注册表（v1 开发中间态兼容）。"""
    (root / "config").mkdir()
    (root / "config/llm.json").write_text(
        json.dumps({"model": "qwen-max"}), encoding="utf-8",
    )
    models = llm_config.list_models(root)
    assert [m["id"] for m in models] == ["qwen-plus"]
    assert llm_config.get_current(root) == "qwen-plus"


def test_corrupt_config_falls_back(root):
    (root / "config").mkdir()
    (root / "config/llm.json").write_text("{broken", encoding="utf-8")
    assert llm_config.list_models(root)[0]["id"] == "qwen-plus"


def test_env_override_model_id_without_config(root, monkeypatch):
    """无配置文件时 SLIRN_LLM_MODEL 覆盖默认模型 id（向后兼容）。"""
    monkeypatch.setenv("SLIRN_LLM_MODEL", "qwen-max")
    assert llm_config.get_current(root) == "qwen-max"
    entry = llm_config.get_current_entry(root)
    assert entry["id"] == "qwen-max"
    assert entry["base_url"] == llm_config.DEFAULT_MODELS[0]["base_url"]


# ---------- CRUD ----------

def test_add_use_remove_roundtrip(root):
    _add(root, **DEEPSEEK)
    assert [m["id"] for m in llm_config.list_models(root)] == ["qwen-plus", "deepseek-chat"]
    assert llm_config.get_current(root) == "qwen-plus"  # 添加不改变当前

    assert llm_config.use_model(root, "deepseek-chat") == "deepseek-chat"
    assert llm_config.get_current(root) == "deepseek-chat"
    entry = llm_config.get_current_entry(root)
    assert entry["base_url"] == DEEPSEEK["base_url"]

    llm_config.remove_model(root, "deepseek-chat")
    assert llm_config.get_current(root) == "qwen-plus"  # 删当前 → 自动切回剩余第一个


def test_remove_only_model_leaves_empty(root):
    llm_config.remove_model(root, "qwen-plus")
    assert llm_config.list_models(root) == []
    assert llm_config.get_current(root) == ""
    assert llm_config.get_current_entry(root) is None


def test_add_first_registered_model_becomes_current(root):
    llm_config.remove_model(root, "qwen-plus")
    _add(root, **DEEPSEEK)
    assert llm_config.get_current(root) == "deepseek-chat"


def test_add_duplicate_rejected(root):
    _add(root, **DEEPSEEK)
    with pytest.raises(llm_config.LLMConfigError, match="已存在"):
        _add(root, **DEEPSEEK)


def test_use_unknown_rejected(root):
    with pytest.raises(llm_config.LLMConfigError, match="未注册"):
        llm_config.use_model(root, "no-such-model")


def test_remove_unknown_rejected(root):
    with pytest.raises(llm_config.LLMConfigError, match="不存在"):
        llm_config.remove_model(root, "no-such-model")


# ---------- 校验 ----------

@pytest.mark.parametrize("bad_id", ["", "bad model", "-lead", "x" * 65, "模型"])
def test_add_invalid_id_rejected(root, bad_id):
    with pytest.raises(llm_config.LLMConfigError, match="模型名不合法"):
        llm_config.add_model(root, bad_id, "p", "https://a.com/v1", "K")


@pytest.mark.parametrize("bad_url", ["", "ftp://x", "not-a-url", "https://"])
def test_add_invalid_base_url_rejected(root, bad_url):
    with pytest.raises(llm_config.LLMConfigError, match="Base URL"):
        llm_config.add_model(root, "m1", "p", bad_url, "K")


@pytest.mark.parametrize("bad_env", ["", "1BAD", "has space", "has-dash"])
def test_add_invalid_env_rejected(root, bad_env):
    with pytest.raises(llm_config.LLMConfigError, match="环境变量名不合法"):
        llm_config.add_model(root, "m1", "p", "https://a.com/v1", bad_env)


def test_add_normalizes_trailing_slash_and_blank_provider(root):
    llm_config.add_model(root, "m1", "  ", "https://a.com/v1/", "MY_KEY")
    m = llm_config.list_models(root)[-1]
    assert m["base_url"] == "https://a.com/v1"
    assert m["provider"] == "未填写厂商"


# ---------- 协议字段（REQ-20260916-001）----------

def test_default_and_anthropic_protocol(root):
    """默认协议 openai；显式 anthropic 落盘可读回。"""
    _add(root, **DEEPSEEK)
    assert llm_config.list_models(root)[0]["protocol"] == "openai"  # 默认注册项
    assert llm_config.list_models(root)[1]["protocol"] == "openai"  # add 缺省
    _add(root, id="claude-sonnet-4-5", provider="Anthropic",
         base_url="https://api.anthropic.com", api_key_env="ANTHROPIC_API_KEY",
         protocol="Anthropic")  # 大小写归一
    m = llm_config.list_models(root)[-1]
    assert m["protocol"] == "anthropic"


def test_invalid_protocol_rejected(root):
    with pytest.raises(llm_config.LLMConfigError, match="协议不合法"):
        _add(root, id="m1", provider="p", base_url="https://a.com/v1",
             api_key_env="K", protocol="grpc")


def test_legacy_entries_without_protocol_normalized(root):
    """v2 落盘条目无 protocol 字段 → 读取时统一补 openai。"""
    (root / "config").mkdir()
    (root / "config/llm.json").write_text(json.dumps({
        "models": [{"id": "old-model", "provider": "p",
                    "base_url": "https://a.com/v1", "api_key_env": "K"}],
        "current": "old-model",
    }), encoding="utf-8")
    assert llm_config.list_models(root)[0]["protocol"] == "openai"


# ---------- 修改（REQ-20260916-001）----------

def test_update_model_fields(root):
    _add(root, **DEEPSEEK)
    llm_config.update_model(root, "deepseek-chat", "deepseek-chat",
                            "DeepSeek官方", "https://api.deepseek.com/v2", "DS_KEY")
    m = llm_config.list_models(root)[-1]
    assert m["provider"] == "DeepSeek官方" and m["base_url"] == "https://api.deepseek.com/v2"
    assert m["api_key_env"] == "DS_KEY" and m["protocol"] == "openai"


def test_update_model_rename_current_follows(root):
    """改名后若原条目是当前模型 → current 跟随新名；位置保持。"""
    _add(root, **DEEPSEEK)
    llm_config.use_model(root, "deepseek-chat")
    llm_config.update_model(root, "deepseek-chat", "deepseek-reasoner",
                            "DeepSeek", DEEPSEEK["base_url"], "DEEPSEEK_API_KEY")
    assert llm_config.get_current(root) == "deepseek-reasoner"
    assert [m["id"] for m in llm_config.list_models(root)] == ["qwen-plus", "deepseek-reasoner"]


def test_update_model_unknown_rejected(root):
    with pytest.raises(llm_config.LLMConfigError, match="不存在"):
        llm_config.update_model(root, "no-such", "x1", "p", "https://a.com/v1", "K")


def test_update_model_rename_to_existing_rejected(root):
    _add(root, **DEEPSEEK)
    with pytest.raises(llm_config.LLMConfigError, match="已存在"):
        llm_config.update_model(root, "deepseek-chat", "qwen-plus",
                                "p", "https://a.com/v1", "K")
