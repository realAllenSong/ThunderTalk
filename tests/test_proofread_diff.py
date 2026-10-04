import pytest

from thundertalk.core.proofread_diff import display_text, page_duration, proofread_diff, proofread_pages, reading_units


@pytest.mark.parametrize('before,after', [
    ('明天再三楼开会。', '明天在三楼开会。'),
    ('use lama index with rag', 'use LlamaIndex with RAG'),
    ('这个 promp 要改一下', '这个 prompt 要改一下'),
    ('GPT的阿修罗、露娜', 'GPT的Astra、Luna'),
    ('test中文𠀀 ok', 'test中文𠀁 OK'),
    ('', 'hello'), ('hello', ''), ('same text', 'same text'),
    ('hello\nworld  !', 'hello world!'),
])
def test_diff_is_lossless(before, after):
    spans = proofread_diff(before, after)
    assert ''.join(s.original for s in spans) == before
    assert ''.join(s.corrected for s in spans) == after
    assert all(s.original == s.corrected for s in spans if s.kind == 'equal')


def test_cjk_character_and_latin_word_boundaries():
    assert [(s.kind, s.original, s.corrected) for s in proofread_diff('明天再三楼用promp。', '明天在三楼用prompt。')] == [
        ('equal', '明天', '明天'), ('replace', '再', '在'), ('equal', '三楼用', '三楼用'),
        ('replace', 'promp', 'prompt'), ('equal', '。', '。')]


def fits_two_lines(text):
    # Deterministic stand-in for the overlay's measured font width.
    lines = text.split('\u2028')
    return sum(max(1, (len(line) + 59) // 60) for line in lines) <= 2


@pytest.mark.parametrize('before,after', [
    ('我们明天再三楼开会。开会后去检察设备。最后记路结果。',
     '我们明天在三楼开会。开会后去检查设备。最后记录结果。'),
    ('Use lama index for retrieval and then test rag with a new promt.',
     'Use LlamaIndex for retrieval and then test RAG with a new prompt.'),
    ('我们用lama index做检索，再用rag生成，最后改promp。',
     '我们用LlamaIndex做检索，再用RAG生成，最后改prompt。'),
])
def test_grouping_preserves_all_changes_with_context(before, after):
    pages = proofread_pages(before, after, fits_two_lines)
    changes = [s for s in proofread_diff(before, after) if s.kind != 'equal']
    displayed = [s for p in pages for s in p.spans if s.kind != 'equal']
    assert displayed == changes
    assert all(p.changes >= 1 and fits_two_lines(display_text(p.spans)) for p in pages)
    assert sum(p.changes for p in pages) == len(changes)


def test_pages_show_the_full_text_inline():
    before = '我们用lama index做检索，再用rag生成，最后改promp。'
    after = '我们用LlamaIndex做检索，再用RAG生成，最后改prompt。'
    page, = proofread_pages(before, after, fits_two_lines)
    assert display_text(page.spans) == ('我们用lama index → LlamaIndex做检索，再用rag → RAG生成，'
                                        '最后改promp → prompt。')


def test_long_text_pages_use_full_width_without_line_breaks():
    before = '。'.join(f'第{i}段我们继续讨论这个方案的细节和实现' for i in range(12))
    after = before.replace('第3段', '第三段').replace('第9段', '第九段')
    pages = proofread_pages(before, after, fits_two_lines)
    assert len(pages) == 2 and all('\u2028' not in display_text(p.spans) for p in pages)
    assert all(len(display_text(p.spans)) > 80 for p in pages)


def test_one_change_excludes_long_unchanged_transcript():
    before = 'unchanged words ' * 100 + 'use lama index with retrieval ' + 'unchanged words ' * 100
    after = before.replace('lama index', 'LlamaIndex')
    pages = proofread_pages(before, after, fits_two_lines)
    assert len(pages) == 1 and pages[0].changes == 1 and pages[0].omitted == 0
    text = display_text(pages[0].spans)
    assert 'lama index → LlamaIndex' in text and text.startswith('…') and text.endswith('…')
    assert len(text) < 120


def test_three_small_changes_can_share_page_when_they_fit():
    pages = proofread_pages('甲。乙。丙。', '一。二。三。', lambda _: True)
    assert len(pages) == 1 and pages[0].changes == 3


def test_many_changes_count_overflow_exactly():
    before = '。'.join(f'第{i}段用错词再继续说明' for i in range(60))
    pages = proofread_pages(before, before.replace('错词', '术语'), fits_two_lines)
    assert sum(p.duration for p in pages) <= 25
    assert pages[-1].omitted > 0
    assert sum(p.changes for p in pages) + pages[-1].omitted == 60
    assert all(p.omitted == 0 for p in pages[:-1])


@pytest.mark.parametrize('before,after', [('旧' * 1000, '新' * 1000), ('x' * 1000, 'y' * 1000),
                                         ('', '新' * 1000), ('旧' * 1000, '')])
def test_very_long_single_change_elides_both_sides(before, after):
    page, = proofread_pages(before, after, fits_two_lines)
    assert page.duration == 6 and page.changes == 1 and page.omitted == 0
    assert fits_two_lines(display_text(page.spans))
    change = next(s for s in page.spans if s.kind != 'equal')
    for text in (change.original, change.corrected):
        if text:
               assert '…' in text and len(text) <= 120


def test_no_changes_has_no_pages():
    assert proofread_pages('same', 'same', fits_two_lines) == []
    assert proofread_pages('', '', fits_two_lines) == []


@pytest.mark.parametrize('units,seconds', [(0, 3.0), (1, 3.17), (10, 4.7), (17, 5.89), (18, 6), (1000, 6)])
def test_timing_clamps(units, seconds):
    assert page_duration(units) == pytest.approx(seconds)


def test_reading_units_are_mixed_language_aware():
    assert reading_units('LlamaIndex加上 RAG') == 4
    assert reading_units('one two three') == 3
    assert reading_units('中文𠀀') == 3
    assert reading_units(' ' * 100) == 0
    assert reading_units('x' * 1000) > 30


def test_timing_uses_full_changes_not_context_or_elided_text():
    page, = proofread_pages('a ' + '错' * 100 + ' z', 'a ' + '对' * 100 + ' z', fits_two_lines)
    assert page.duration == 6
    page, = proofread_pages('context wrong words context', 'context correct words context', fits_two_lines)
    assert page.duration == pytest.approx(3.17)
