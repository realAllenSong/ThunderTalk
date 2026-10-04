import pytest

from thundertalk.core.proofread_diff import proofread_diff


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
