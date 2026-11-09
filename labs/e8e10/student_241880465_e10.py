"""验证配置宏改变实际依赖，并保存五次完整对照与计时。"""
import argparse
import csv
import json
from pathlib import Path
from statistics import median
from lab import fixture, commit, scan, save, buildchecker


def summarize(out):
    """按场景汇总原始计时，保留中位数、范围和真实加速比。"""
    with (out / 'results.csv').open(encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    summary = []
    for case in dict.fromkeys(row['case'] for row in rows):
        group = [r for r in rows if r['case'] == case]
        assert len(group) == 5 and all(r['equal'] == 'True' for r in group)
        inc = [float(r['incremental_s']) for r in group]
        full = [float(r['full_s']) for r in group]
        summary.append(dict(case=case, n=5, inc_median_s=median(inc), full_median_s=median(full), inc_min_s=min(inc), inc_max_s=max(inc), full_over_inc=median(full)/median(inc)))
    with (out / 'performance-summary.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    return summary


def configuration_case(out, dynamic=False):
    """只改变 MODE 编译宏，断言条件包含新增，重复五次且固定历史输入。"""
    project = out / 'project'
    fixture(project)
    (project / 'config.h').write_text('#ifdef MODE\n#include "extra.h"\n#endif\n#define VALUE 1\n', encoding='utf-8')
    commit(project, '建立条件包含基线')
    baseline = scan(project)
    save(out / 'baseline.json', baseline)
    assert baseline['actual'] == {'main.o': ['config.h', 'main.c'], 'helper.o': ['helper.c']}
    source = (project / 'config.h').read_bytes()
    makefile = project / 'Makefile'
    makefile.write_text(makefile.read_text().replace('-O0', '-O0 -DMODE=1'), encoding='utf-8')
    commit(project, '启用 MODE 宏以激活条件依赖')
    rows = []
    for repeat in range(1, 6):
        inc, full = scan(project, baseline), scan(project)
        save(out / f'configuration-r{repeat}-incremental.json', inc)
        save(out / f'configuration-r{repeat}-full.json', full)
        assert all(inc[k] == full[k] for k in ('actual', 'declared', 'findings'))
        assert inc['actual'] == {'main.o': ['config.h', 'extra.h', 'main.c'], 'helper.o': ['helper.c']}
        assert inc['declared'] == {'main.o': ['main.c', 'unused.h'], 'helper.o': ['helper.c']}
        assert {(f['type'], f['target'], f['dependency']) for f in inc['findings']} == {('MISSING', 'main.o', 'config.h'), ('MISSING', 'main.o', 'extra.h'), ('REDUNDANT', 'main.o', 'unused.h')}
        assert inc['added'] == [['MISSING', 'main.o', 'extra.h']] and inc['resolved'] == []
        assert inc['reused'] == [] and inc['refreshed'] == ['main.o', 'helper.o']
        assert (project / 'config.h').read_bytes() == source
        rows.append(dict(case='configuration', repeat=repeat, equal=True, incremental_s=inc['elapsed_s'], full_s=full['elapsed_s'], reused=0, refreshed=2))
    with (out / 'results.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summarize(out)
    if dynamic:
        buildchecker(project, out / 'final-buildchecker')
    save(out / 'gate.json', dict(passed=True, rows=5, dynamic=dynamic))


def verify_standard(out):
    """检查标准矩阵的独立预期，并从原始三十次计时生成汇总。"""
    summary = summarize(out)
    assert [r['case'] for r in summary] == ['unchanged', 'body', 'include', 'declaration', 'flags', 'header']
    for row in summary:
        case = row['case']
        report = json.loads((out / (case + '.json')).read_text())
        reuse = case in ('unchanged', 'body', 'declaration')
        assert report['reused'] == (['main.o', 'helper.o'] if reuse else [])
        assert report['refreshed'] == ([] if reuse else ['main.o', 'helper.o'])
        expected = {('REDUNDANT', 'main.o', 'unused.h')}
        if case in ('unchanged', 'body', 'include'):
            expected.add(('MISSING', 'main.o', 'config.h'))
        if case == 'include':
            expected.add(('MISSING', 'main.o', 'extra.h'))
        assert {(f['type'], f['target'], f['dependency']) for f in report['findings']} == expected
    print('标准三十行及独立预期通过')


def main():
    """核对已完成标准实验，在新目录执行个人扩展。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--standard', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    verify_standard(args.standard)
    args.out.mkdir(parents=True, exist_ok=False)
    configuration_case(args.out, dynamic=True)
    print('个人配置变化五次及动态交叉核对通过')


if __name__ == '__main__':
    main()
