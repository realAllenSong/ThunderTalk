from thundertalk.core import music_rules as rules


def test_listed_artists_in_english_and_chinese():
    text = 'Taylor Swift, 周杰伦 and ADELE'
    spans = rules.find_artist_mentions(text, ['Taylor Swift', '周杰伦', 'Adele'])
    assert [s.text for s in spans] == ['Taylor Swift', '周杰伦', 'ADELE']
    assert all(text[s.start:s.end] == s.text for s in spans)
    assert not rules.find_artist_mentions('adelery, indie jazz', ['Adele'])


def test_ambiguous_names_need_a_cue():
    names = ['Queen', 'Yes', 'Muse', 'Genesis', 'Rush']
    assert not rules.find_artist_mentions('yes, a queen; muse, genesis and rush', names)
    assert [s.text for s in rules.find_artist_mentions('sounds like Queen', names)] == ['Queen']
    assert [s.text for s in rules.find_artist_mentions('Muse-style guitar', names)] == ['Muse']


def test_unknown_style_and_cover_requests():
    for text in ('in the style of Some Unknown Person', 'sounds like Nobody',
                 'Nobody-style', '像某某一样', '某某风格', '模仿某某', '翻唱'):
        assert rules.find_artist_mentions(text, [])
    assert not rules.find_artist_mentions('folk pop, warm mood, acoustic guitar', [])


def test_remove_keeps_unicode_offsets_and_other_text():
    text = '轻柔周杰伦 guitar and Adele, piano'
    spans = rules.find_artist_mentions(text, ['周杰伦', 'Adele'])
    assert rules.remove_mentions(text, spans) == '轻柔 guitar and , piano'


def test_shipped_list_has_both_languages():
    names = rules.artist_names()
    assert len(names) >= 5000
    assert 'Taylor Swift' in names and 'Queen' in names and '周杰伦' in names
