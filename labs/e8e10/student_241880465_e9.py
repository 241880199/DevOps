"""保存 E9 连续提交证据，并检查多层间接依赖的切换。"""
import argparse
import time
from pathlib import Path

from lab import commit, fixture, run, save, scan


def findings(report):
    """将发现转成集合，避免报告排序影响独立预期比较。"""
    return {(f['type'], f['target'], f['dependency']) for f in report['findings']}


def check_full(project, report):
    """将增量结果与不传历史的全量扫描逐字段比较。"""
    full = scan(project)
    for field in ('actual', 'declared', 'findings'):
        assert report[field] == full[field], field


def indirect_switch(out):
    """只改中间头文件的 include，验证多层依赖切换与声明修复。"""
    project = out / 'project'
    fixture(project)
    (project / 'config.h').write_text('#include "extra.h"\n#define VALUE 1\n', encoding='utf-8')
    (project / 'extra.h').write_text('#include "leaf_a.h"\n', encoding='utf-8')
    for name in ('leaf_a.h', 'leaf_b.h'):
        (project / name).write_text('/* 预先存在的叶子头文件 */\n', encoding='utf-8')
    commit(project, '建立多层头文件基线')
    before = scan(project)
    save(out / 'baseline.json', before)
    source = (project / 'main.c').read_bytes()
    makefile = (project / 'Makefile').read_text()
    assert before['actual'] == {'main.o': ['config.h', 'extra.h', 'leaf_a.h', 'main.c'], 'helper.o': ['helper.c']}
    (project / 'extra.h').write_text('#include "leaf_b.h"\n', encoding='utf-8')
    commit(project, '仅切换中间头文件的间接包含')
    changed = scan(project, before)
    save(out / 'switched.json', changed)
    assert changed['key'] != before['key']
    assert changed['reused'] == [] and changed['refreshed'] == ['main.o', 'helper.o']
    assert changed['actual'] == {'main.o': ['config.h', 'extra.h', 'leaf_b.h', 'main.c'], 'helper.o': ['helper.c']}
    assert changed['added'] == [['MISSING', 'main.o', 'leaf_b.h']]
    assert changed['resolved'] == [['MISSING', 'main.o', 'leaf_a.h']]
    assert findings(changed) == {('MISSING', 'main.o', n) for n in ('config.h', 'extra.h', 'leaf_b.h')} | {('REDUNDANT', 'main.o', 'unused.h')}
    assert (project / 'main.c').read_bytes() == source
    assert (project / 'Makefile').read_text() == makefile
    check_full(project, changed)
    (project / 'Makefile').write_text(makefile.replace('main.c unused.h', 'main.c unused.h config.h extra.h leaf_b.h'), encoding='utf-8')
    commit(project, '补齐当前多层依赖声明')
    repaired = scan(project, changed)
    save(out / 'repaired.json', repaired)
    assert repaired['key'] == changed['key']
    assert repaired['reused'] == ['main.o', 'helper.o'] and repaired['refreshed'] == []
    assert repaired['added'] == []
    assert repaired['resolved'] == [['MISSING', 'main.o', n] for n in ('config.h', 'extra.h', 'leaf_b.h')]
    assert findings(repaired) == {('REDUNDANT', 'main.o', 'unused.h')}
    check_full(project, repaired)
    print('E9_INDIRECT_SWITCH_PASS')


def standard(out):
    """按手册顺序构建 C0–C3，保存日志并核对每次实际变化。"""
    project = out / 'project'
    fixture(project)
    base_findings = {('MISSING', 'main.o', 'config.h'), ('REDUNDANT', 'main.o', 'unused.h')}
    (out / 'C0-build.log').write_text(run(['make', 'app'], project), encoding='utf-8')
    assert run(['./app'], project).strip() == '1'
    previous = scan(project)
    save(out / 'baseline.json', previous)
    assert findings(previous) == base_findings
    for stage in ('C1', 'C2', 'C3'):
        if stage != 'C3':
            time.sleep(1.05)
            source = project / 'main.c'
            text = source.read_text()
            source.write_text(text.replace('VALUE+helper()', 'VALUE+helper()+0') if stage == 'C1' else '#include "extra.h"\n' + text, encoding='utf-8')
        else:
            makefile = project / 'Makefile'
            makefile.write_text(makefile.read_text().replace('main.c unused.h', 'main.c unused.h config.h extra.h'), encoding='utf-8')
        commit(project, {'C1': 'C1 body change', 'C2': 'C2 include change', 'C3': 'C3 repair declarations'}[stage])
        if stage != 'C3':
            log = run(['make', 'app'], project)
            (out / (stage + '-build.log')).write_text(log, encoding='utf-8')
            assert '-c main.c -o main.o' in log and 'gcc main.o helper.o -o app' in log
            assert '-c helper.c' not in log
            assert run(['./app'], project).strip() == '1'
        report = scan(project, previous)
        save(out / (stage + '.json'), report)
        if stage == 'C1':
            assert report['reused'] == ['main.o', 'helper.o'] and report['refreshed'] == []
            assert findings(report) == base_findings and report['added'] == report['resolved'] == []
        elif stage == 'C2':
            assert report['refreshed'] == ['main.o', 'helper.o'] and report['reused'] == []
            assert findings(report) == base_findings | {('MISSING', 'main.o', 'extra.h')}
            assert report['added'] == [['MISSING', 'main.o', 'extra.h']] and report['resolved'] == []
        else:
            assert report['reused'] == ['main.o', 'helper.o'] and report['refreshed'] == []
            assert report['resolved'] == [['MISSING', 'main.o', 'config.h'], ['MISSING', 'main.o', 'extra.h']]
            assert findings(report) == {('REDUNDANT', 'main.o', 'unused.h')} and report['added'] == []
        check_full(project, report)
        print(stage, report['commit'], 'PASS')
        previous = report
    history = run(['git', 'rev-list', '--reverse', 'HEAD'], project).splitlines()
    assert len(history) == 4
    (out / 'history.txt').write_text(run(['git', 'log', '--reverse', '--format=%H %s'], project), encoding='utf-8')


def main():
    """创建全新证据目录；拒绝覆盖已完成的实验记录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    standard(args.out / 'standard')
    indirect_switch(args.out / 'extension')
    save(args.out / 'result.json', {'standard': 'PASS', 'indirect_switch': 'PASS'})


if __name__ == '__main__':
    main()
