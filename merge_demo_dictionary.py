# -*- coding: utf-8 -*-
"""
词典合并工具
将演示专用词典(dictionary_demo.json)合并到主词典(dictionary.json)
演示词条不会覆盖主词典中已有的同名词条（主词典优先）
"""
import json
import os
import sys
import shutil
from datetime import datetime


def load_dict(path):
    """加载词典，过滤注释键"""
    with open(path, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    d = {}
    comments = {}
    for k, v in raw.items():
        if k.startswith('___') or k.startswith('===') or v == '':
            comments[k] = v
            continue
        if k.startswith('//') or k.startswith('#'):
            comments[k] = v
            continue
        d[k.strip()] = v
    return d, comments


def save_dict(path, data, comments):
    """保存词典"""
    # 先写注释，再写词条
    output = {}
    for k, v in comments.items():
        output[k] = v
    for k, v in sorted(data.items(), key=lambda x: x[0]):
        output[k] = v
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    main_dict_path = os.path.join(script_dir, 'dictionary.json')
    demo_dict_path = os.path.join(script_dir, 'dictionary_demo.json')

    if not os.path.exists(main_dict_path):
        print(f"错误: 主词典不存在: {main_dict_path}")
        sys.exit(1)
    if not os.path.exists(demo_dict_path):
        print(f"错误: 演示词典不存在: {demo_dict_path}")
        sys.exit(1)

    # 备份主词典
    backup_path = main_dict_path + f'.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    shutil.copy2(main_dict_path, backup_path)
    print(f"已备份主词典: {backup_path}")

    # 加载两个词典
    main_dict, main_comments = load_dict(main_dict_path)
    demo_dict, demo_comments = load_dict(demo_dict_path)

    print(f"\n主词典词条数: {len(main_dict)}")
    print(f"演示词典词条数: {len(demo_dict)}")

    # 合并：主词典优先，演示词典中新增的词条追加
    added = 0
    skipped = 0
    for k, v in demo_dict.items():
        if k not in main_dict:
            main_dict[k] = v
            added += 1
        else:
            skipped += 1

    print(f"\n合并结果:")
    print(f"  新增词条: {added}")
    print(f"  跳过(主词典已有): {skipped}")
    print(f"  合并后总词条: {len(main_dict)}")

    # 合并注释
    all_comments = {**main_comments}
    for k, v in demo_comments.items():
        if k not in all_comments:
            all_comments[k] = v

    # 保存
    save_dict(main_dict_path, main_dict, all_comments)
    print(f"\n已保存合并后的词典: {main_dict_path}")
    print("\n✓ 词典合并完成！可以启动应用进行演示。")


if __name__ == '__main__':
    main()
