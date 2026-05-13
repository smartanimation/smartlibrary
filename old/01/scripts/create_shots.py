import os
import yaml
import gspread
import sys

class SimpleTemplate:
    """Python 3.12対応: 変数置換を行う自作クラス"""
    def __init__(self, name, pattern):
        self.name = name
        self.pattern = pattern

    def format(self, data):
        try:
            clean_data = {k: str(v) for k, v in data.items()}
            return os.path.normpath(self.pattern.format(**clean_data))
        except (KeyError, IndexError):
            return os.path.normpath(self.pattern)

def load_shot_configs(config_dir):
    """設定ファイルを読み込み、テンプレートと部署リストを返す"""
    base_path = os.path.join(config_dir, 'templates_base.yml')
    shots_path = os.path.join(config_dir, 'templates_shots.yml')

    with open(base_path, 'r', encoding='utf-8') as f:
        base_cfg = yaml.safe_load(f)
    with open(shots_path, 'r', encoding='utf-8') as f:
        shots_cfg = yaml.safe_load(f)

    project_root = base_cfg['anchors'].get('project_root', '')
    # templates_base.yml から Shot用の部署リストを取得
    depts = base_cfg['anchors'].get('shot_depts', [])
    
    raw_templates = shots_cfg.get('templates', {})
    
    # テンプレートの入れ子解消 (project_root 等を解決)
    resolved = {n: p.replace("{project_root}", project_root) for n, p in raw_templates.items()}
    for _ in range(3):
        for n, p in resolved.items():
            for on, op in resolved.items():
                target = "{" + on + "}"
                if target in p:
                    resolved[n] = p.replace(target, op)
                    p = resolved[n]

    templates = {n: SimpleTemplate(n, pat) for n, pat in resolved.items()}
    return templates, depts

def main():
    config_dir = os.environ.get("PROJECT_CONFIG_DIR")
    if not config_dir:
        print("ERROR: PROJECT_CONFIG_DIR が未設定です。")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    creds_path = os.path.join(script_dir, 'credentials.json')

    try:
        templates, depts = load_shot_configs(config_dir)
        gc = gspread.service_account(filename=creds_path)
        sh = gc.open("Shot_List").sheet1 
        rows = sh.get_all_records()
    except Exception as e:
        print(f"Error: {e}")
        return

    # Status列は D列 (4列目) に統一
    STATUS_COL_INDEX = 4 

    print(f"--- Shot Creation (Work/Dept Structure) ---")

    for i, row in enumerate(rows, start=2):
        status = str(row.get('Status', '')).strip()
        if status != "Wait":
            continue

        try:
            episode = str(row.get('Episode', '0')).zfill(2)
            seq = str(row.get('Sequence', '0')).zfill(3)
            shot = str(row.get('Shot', '0')).zfill(4)
        except:
            print(f"Skipping row {i}: Invalid Data")
            continue
        
        print(f"\n[Processing] Ep{episode}_Seq{seq}_Shot{shot}")

        # 基本データ（部署を含まない）
        base_data = {
            "episode": episode,
            "seq": seq,
            "shot": shot,
            "version": "v001"
        }

        try:
            # 1. 共通フォルダの生成 (dept を含まないテンプレート: data, publish, render等)
            for t_name, template in templates.items():
                if t_name.startswith("dept_") and "{dept}" not in template.pattern:
                    path = template.format(base_data)
                    if "{" not in path:
                        if not os.path.exists(path):
                            print(f"  [Creating Base] -> {path}")
                            os.makedirs(path, exist_ok=True)

            # 2. 部署別フォルダの生成 (work/{dept} など)
            for dept_name in depts:
                current_data = base_data.copy()
                current_data["dept"] = dept_name
                
                for t_name, template in templates.items():
                    if t_name.startswith("dept_") and "{dept}" in template.pattern:
                        path = template.format(current_data)
                        if "{" not in path:
                            if not os.path.exists(path):
                                print(f"  [Creating Dept] -> {path}")
                                os.makedirs(path, exist_ok=True)

            # スプレッドシートを WIP に更新 (D列)
            #sh.update_cell(i, STATUS_COL_INDEX, "WIP")
            print(f"  -> Successfully updated Status to WIP.")

        except Exception as e:
            print(f"  -> ERROR: {e}")

    print(f"\nAll processes finished.")

if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")