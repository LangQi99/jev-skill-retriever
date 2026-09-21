#!/usr/bin/env bash
# Install jev-skill-retriever as a Hermes plugin.

set -e

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_NAME="jev-skill-retriever"
PLUGIN_DEST="$HERMES_HOME/plugins/$PLUGIN_NAME"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "⏳ Installing $PLUGIN_NAME..."

# ── 1. Copy plugin + src + community skills to Hermes plugins dir ──
echo ""
echo "  1/4  Copying files..."
mkdir -p "$PLUGIN_DEST"
cp -r "$REPO_DIR"/plugin/* "$PLUGIN_DEST/"
cp -r "$REPO_DIR"/src "$PLUGIN_DEST/"
# Copy community skills (230 permissively-licensed)
if [ -d "$REPO_DIR/src/skill_retriever/community_skills" ]; then
    mkdir -p "$PLUGIN_DEST/src/skill_retriever/community_skills"
    cp -r "$REPO_DIR/src/skill_retriever/community_skills"/* "$PLUGIN_DEST/src/skill_retriever/community_skills/"
    echo "  ✅ Community skills copied ($(ls "$REPO_DIR/src/skill_retriever/community_skills/" | grep -v LICENSES.json | wc -l) skills)"
fi
# Copy ship-safe tree as default
mkdir -p "$PLUGIN_DEST/src/skill_retriever/capability_tree"
for f in tree_10000_ship_safe.yaml tree_10000_ship_safe.html tree_10000.yaml tree_10000.html; do
    src="$REPO_DIR/src/skill_retriever/capability_tree/$f"
    [ -f "$src" ] && cp "$src" "$PLUGIN_DEST/src/skill_retriever/capability_tree/"
done
echo "  ✅ Files copied to $PLUGIN_DEST"

# ── 2. Install Python deps ──
echo ""
echo "  2/4  Installing dependencies..."
VENV_PIP="$HERMES_HOME/hermes-agent/venv/bin/pip"
if [ -f "$VENV_PIP" ]; then
    HERMES_PYTHON="${VENV_PIP%/pip}/python"
    "$VENV_PIP" install chromadb litellm pyyaml python-dotenv rich --quiet 2>/dev/null && \
        echo "  ✅ Dependencies installed" || \
        echo "  ⚠️  Some deps failed — check manually: pip install chromadb litellm pyyaml python-dotenv rich"
else
    HERMES_PYTHON="python3"
    echo "  ⚠️  Hermes venv not found at $VENV_PIP"
    echo "     Install manually: pip install chromadb litellm pyyaml python-dotenv rich"
fi

# ── 3. Enable in config ──
echo ""
echo "  3/4  Enabling plugin..."
CONFIG="$HERMES_HOME/config.yaml"
if [ -f "$CONFIG" ]; then
    python3 -c "
import yaml
with open('$CONFIG', 'r') as f:
    c = yaml.safe_load(f) or {}
c.setdefault('plugins', {}).setdefault('enabled', [])
if '$PLUGIN_NAME' not in c['plugins']['enabled']:
    c['plugins']['enabled'].append('$PLUGIN_NAME')
with open('$CONFIG', 'w') as f:
    yaml.dump(c, f, default_flow_style=False, allow_unicode=True)
"
    echo "  ✅ Plugin enabled in config.yaml"
else
    echo "  ⚠️  Config not found at $CONFIG"
fi

# ── 4. Verify installation ──
echo ""
echo "  4/4  Verifying imports..."
cd "$PLUGIN_DEST"
PYTHONPATH="$PLUGIN_DEST/src" "$HERMES_PYTHON" -c "
import sys
sys.path.insert(0, 'src')
try:
    from skill_retriever import Searcher, SearchResult, TreeNode, Skill
    from skill_retriever.cli import main
    from skill_retriever.search.searcher import Searcher
    from skill_retriever.scanner import scan_hermes_skills
    from skill_retriever.config import CAPABILITY_TREE_PATH
    print(f'  All core imports OK')
    print(f'  Default tree: {CAPABILITY_TREE_PATH.name}')
except ImportError as e:
    print(f'  Import failed: {e}')
    print('  Some features may be unavailable until missing deps are installed.')
" || echo "  Import check failed (non-fatal)"

SKILL_RETRIEVER_CACHE_DIR="$HERMES_HOME/skill-retriever-cache" \
PYTHONPATH="$PLUGIN_DEST/src" \
    "$HERMES_PYTHON" -m skill_retriever.build_flat_index

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ $PLUGIN_NAME installed!"
echo ""
echo "  Next steps:"
echo "    1. Add TYPESAFE_API_KEY to $HERMES_HOME/.env"
echo "    2. Restart Hermes:  hermes gateway restart"
echo "    3. Send a request and check the Hermes log for jev-skill-retriever"
echo ""
echo "  System prompt recommendations (see README):"
echo "    - Ensure <available_skills> uses COMPACT format (categories + counts)"
echo "    - Remove vestigial full skills_list if present (~8K tokens saved)"
echo "    - Keep compact block for zero-latency awareness (~1.2K tokens)"
echo ""
echo "  To disable:"
echo "    SKILL_RETRIEVER_DISABLE=1"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
