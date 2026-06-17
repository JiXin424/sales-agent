#!/bin/bash

# ============================================
# Claude Code 项目设置快捷配置脚本
# ============================================

SETTINGS_FILE="$HOME/.claude/settings.json"

# 确保 ~/.claude 目录存在
mkdir -p "$HOME/.claude"

# 确保 settings.json 存在
if [ ! -f "$SETTINGS_FILE" ]; then
    echo '{}' > "$SETTINGS_FILE"
fi


apply_config() {
    local label="$1"
    local json="$2"

    echo ""
    echo ">>> 应用 ${label} 配置..."

    # 备份原文件
    cp "$SETTINGS_FILE" "${SETTINGS_FILE}.bak.$(date +%Y%m%d%H%M%S)"

    # 覆盖写入
    echo "$json" > "$SETTINGS_FILE"
    echo ">>> 配置完成！"
}

CONFIG_ONE='{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "222de8e210e344c8b4852e6ae00636ce.TyI2MAJDLZoxxZbT",
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": 1,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5-turbo",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2"
  }
}'

CONFIG_TWO='{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "ark-e850b22a-33ff-426e-b3d8-8b5166b9d239-320fe",
    "ANTHROPIC_BASE_URL": "https://ark.cn-beijing.volces.com/api/coding",
    "ANTHROPIC_MODEL": "deepseek-v4-pro"
  }
}'

CONFIG_THREE='{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "01fcfa565c1f487eaf55fc3fa282b3b1.rIp3vBoTJzqREZzf",
    "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
    "API_TIMEOUT_MS": "3000000",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": 1,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5-turbo",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2"
  }
}'

CONFIG_FOUR='{
  "env": {
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "sk-d4cb4885e76b4a6ab7ce168dd5181627",
    "ANTHROPIC_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_EFFORT_LEVEL": "max"
  }
}'

echo "============================================"
echo "  Claude Code 项目设置 - 配置选择"
echo "============================================"
echo ""
echo "  1) glm(泰山-new)"
echo "  2) 火山引擎(gtj)"
echo "  3) glm(泰山)"
echo "  4) deepseek(gtj)"
echo ""
echo "============================================"
read -p "请选择 [1-4]: " choice

case $choice in
    1) apply_config "glm(泰山-new)" "$CONFIG_ONE" ;;
    2) apply_config "火山引擎(gtj)" "$CONFIG_TWO" ;;
    3) apply_config "glm(泰山)" "$CONFIG_THREE" ;;
    4) apply_config "deepseek(gtj)" "$CONFIG_FOUR" ;;
    *)
        echo "无效选择，退出。"
        exit 1
        ;;
esac
