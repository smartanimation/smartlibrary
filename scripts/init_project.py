import os
import yaml

class SimpleTemplate:
    """変数置換を行う自作クラス"""
    def __init__(self, name, pattern):
        self.name = name
        self.pattern = pattern

    def format(self, data):
        try:
            clean_data = {k: str(v) for k, v in data.items()}
            return os.path.normpath(self.pattern.format(**clean_data))
        except:
            return os.path.normpath(self.pattern)

def resolve_templates(raw_templates, project_root):
    """テンプレート内の {variable} を再帰的に置換して物理パスにする"""
    resolved = {}
    # 1. まず {project_root} を物理パスに置き換える
    for name, pattern in raw_templates.items():
        resolved[name] = pattern.replace("{project_root}", project_root)

    # 2. テンプレート同士の参照（{production_root}等）を解決
    for _ in range(3):
        for name, pattern in resolved.items():
            for other_name, other_pattern in resolved.items():
                target = "{" + other_name + "}"
                if target in pattern:
                    resolved[name] = pattern.replace(target, other_pattern)
                    pattern = resolved[name]
    return resolved

def main():
    # 1. 環境変数の取得
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if not config_dir:
        print("ERROR: PROJECT_CONFIG_DIR が未設定です。")
        return

    yml_path = os.path.join(config_dir, 'templates_base.yml')
    if not os.path.exists(yml_path):
        print(f"ERROR: {yml_path} が見つかりません。")
        return

    # 2. YAMLの読み込み
    with open(yml_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    anchors = cfg.get('anchors', {})
    project_root = anchors.get('project_root', '')
    shot_depts = anchors.get('shot_depts', [])
    asset_depts = anchors.get('asset_depts', [])
    raw_templates = cfg.get('templates', {})

    # 3. テンプレートパスの解決
    resolved_paths = resolve_templates(raw_templates, project_root)
    
    print(f"--- Project Initialization ---")

    # 4. 主要なディレクトリの生成
    for name, path in resolved_paths.items():
        if "{" in path: continue # 未解決の変数が残っていればスキップ
        try:
            os.makedirs(path, exist_ok=True)
            print(f"[Created/Exists] {name}: {path}")
        except Exception as e:
            print(f"[Failed] {name}: {e}")

    # 5. library_root 直下への部署フォルダ生成
    library_path = resolved_paths.get('library_root')
    if library_path:
        print(f"\n--- Generating Library Dept Folders ---")
        # 両方の部署リストを結合
        all_depts = list(set(shot_depts + asset_depts))
        
        print(all_depts)
        return
        for dept in all_depts:
            dept_path = os.path.join(library_path, dept)
            try:
                os.makedirs(dept_path, exist_ok=True)
                print(f"[Library Dept] {dept}: {dept_path}")
            except Exception as e:
                print(f"[Library Failed] {dept}: {e}")

if __name__ == "__main__":
    main()
    #input("\nPress Enter to exit...")