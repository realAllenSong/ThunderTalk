"""Built-in voice styles: the kinds of voices people know from short-video,
vlog and commentary content, designed from scratch by VoxCPM2. Each is an
original voice made from a description — none imitates a real person, a
platform's product voice or a character.

The line each voice speaks matters as much as the description: its tone and
rhythm are copied into everything the voice later says, so every style gets
a line in its own natural register (a flat neutral sentence makes a flat voice).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    slug: str
    name_zh: str
    name_en: str
    language: str        # chinese | english
    gender: str          # f | m
    group: str           # narration | lifestyle | fun | english
    instruct: str        # VoxCPM2 voice-design description
    line: str            # what the reference clip says


STYLES: tuple[Style, ...] = (
    # ── narration / commentary ───────────────────────────────────────────
    Style("movie-commentary", "影视解说", "Film recap", "chinese", "m", "narration",
          "中年男性，影视解说风格，嗓音低沉有磁性，语速偏快，节奏紧凑，带着悬念感",
          "注意看，这个男人叫小帅。他怎么也没想到，自己只是下楼买了个早饭，就被卷进了一场惊天大案。"),
    Style("documentary", "纪录片旁白", "Documentary", "chinese", "m", "narration",
          "浑厚沉稳的中年男声，纪录片旁白，语速舒缓，饱含感情，富有感染力",
          "在遥远的青藏高原上，生活着一群顽强的生灵。它们在严寒中寻找食物，也在风雪里守护着彼此。"),
    Style("news", "新闻主播", "News anchor", "chinese", "f", "narration",
          "专业的女新闻主播，字正腔圆，声音清亮，庄重而亲切",
          "各位观众晚上好，欢迎收看今天的新闻。今天上午，全国多地迎来降温天气，气象部门提醒大家注意添衣保暖。"),
    Style("bedtime", "睡前故事", "Bedtime story", "chinese", "f", "narration",
          "温暖柔和的年轻妈妈，给孩子讲睡前故事，语速很慢，轻声细语，充满爱意",
          "从前呀，在一片大森林里，住着一只小兔子。每天晚上，它都会抬头看星星，想着星星上面，会住着谁呢？"),
    Style("grandpa", "老爷爷", "Grandpa", "chinese", "m", "narration",
          "和蔼可亲的老爷爷，声音苍老略带沙哑，语速缓慢，慈祥温暖",
          "孩子啊，爷爷年轻的时候，也跟你一样，总想着出去闯一闯。后来才明白，家才是最好的地方。"),
    Style("ceo", "霸道总裁", "Cold CEO", "chinese", "m", "narration",
          "低沉磁性的年轻男声，霸道总裁，语气冷静克制，带着压迫感，语速不快",
          "这件事，我说了算。三天之内，我要看到结果。记住，别让我失望。"),
    # ── lifestyle ────────────────────────────────────────────────────────
    Style("bestie", "温柔闺蜜", "Gentle bestie", "chinese", "f", "lifestyle",
          "二十多岁的年轻女生，声音温柔甜美，像在跟闺蜜聊天分享好物，自然亲切，带着笑意",
          "姐妹们，这个真的要吹爆！我用了一个多月，皮肤状态肉眼可见地变好了，今天就把我的私藏分享给你们。"),
    Style("genki-girl", "元气少女", "Bubbly girl", "chinese", "f", "lifestyle",
          "活泼开朗的少女，元气满满，语调上扬，充满笑意，语速稍快",
          "哈喽大家好呀！今天天气超级好，我们一起去探店吧，听说这家的甜品特别特别好吃！"),
    Style("sunny-guy", "阳光男生", "Sunny guy", "chinese", "m", "lifestyle",
          "二十岁出头的阳光男生，声音清爽干净，语气轻松自然，像在跟朋友聊天",
          "兄弟们，今天带你们体验一下这款新游戏。说实话，第一眼我就被它的画面惊艳到了，咱们直接开玩。"),
    Style("elegant", "知性御姐", "Poised woman", "chinese", "f", "lifestyle",
          "三十岁左右的成熟女性，知性从容，声音略低有质感，语速适中，自信大方",
          "职场上最重要的，不是你有多聪明，而是你能不能把复杂的事情，用最简单的话讲清楚。"),
    Style("whisper", "耳语助眠", "Whisper", "chinese", "f", "lifestyle",
          "轻声耳语的温柔女声，气声很重，声音很轻很近，像在耳边说悄悄话，非常放松",
          "嘘，放松下来。慢慢闭上眼睛，深呼吸。今天辛苦啦，好好休息吧。"),
    # ── fun / dialects ───────────────────────────────────────────────────
    Style("dongbei", "东北老铁", "Dongbei buddy", "chinese", "m", "fun",
          "东北话，幽默风趣的中年大哥，嗓门大，热情豪爽，说话带劲",
          "哎呀妈呀，老铁们，这玩意儿老带劲了！咱东北人说话就是实在，好使就是好使，绝对不忽悠你。"),
    Style("sichuan", "四川辣妹", "Sichuan sister", "chinese", "f", "fun",
          "四川话，泼辣爽快的年轻女生，语气热情，节奏明快",
          "哎呀，你们晓得不嘛，这家火锅简直巴适得板！麻辣鲜香，吃一口就停不下来，赶紧喊起朋友一起来嘛。"),
    Style("cantonese", "港味粤语", "Cantonese", "chinese", "m", "fun",
          "粤语，香港本地的中年大叔，语气轻松幽默，市井亲切",
          "各位朋友大家好，今日带大家去食一间好出名嘅茶餐厅，佢哋嘅菠萝包真系一流，唔试过就走宝啦。"),
    Style("taiwan", "台湾腔", "Taiwanese accent", "chinese", "f", "fun",
          "台湾口音的年轻女生，说话软软糯糯的，语气可爱，尾音拖长",
          "欸你知道吗，我昨天去夜市吃到超好吃的鸡排，真的是超级无敌好吃的啦，下次一定要带你去！"),
    Style("kid", "萌娃", "Little kid", "chinese", "m", "fun",
          "五六岁的小男孩，奶声奶气，天真可爱，说话有点慢",
          "妈妈妈妈，你看天上的云朵，像不像一只大白兔呀？我好想摸一摸它哦。"),
    Style("lazy", "摆烂青年", "Slacker", "chinese", "m", "fun",
          "慵懒拖沓的年轻男声，有气无力，带点丧和无奈，语速很慢，很好笑",
          "唉，又是周一，我真的不想上班。能不能让我再睡五分钟，就五分钟，求求了。"),
    Style("trickster", "机灵鬼", "Trickster", "chinese", "m", "fun",
          "调皮机灵的年轻男声，语速很快，声音尖细夸张，得意洋洋，很搞笑",
          "嘿嘿，俺来也！这点小事儿可难不倒俺，看俺一个跟头翻过去，保证给你办得妥妥的！"),
    # ── English ──────────────────────────────────────────────────────────
    Style("youtuber", "YouTube 博主", "YouTuber", "english", "m", "english",
          "An energetic young American man, YouTube tutorial host, friendly, upbeat and natural",
          "Hey guys, welcome back to the channel! Today I'm going to show you three simple tricks "
          "that will totally change the way you edit your videos."),
    Style("vlogger", "Vlog 女生", "Vlogger", "english", "f", "english",
          "A cheerful young American woman, casual vlog style, warm, chatty and natural",
          "Okay, so I just got back from the farmers market, and honestly, I found the cutest little "
          "coffee shop on the way home."),
    Style("trailer", "预告片", "Trailer voice", "english", "m", "english",
          "A deep, dramatic middle-aged man, movie trailer narrator, slow, powerful and gravelly",
          "In a world where nothing is what it seems, one man will risk everything to uncover the truth."),
    Style("british", "英式旁白", "British narrator", "english", "f", "english",
          "A calm British woman in her forties, nature documentary narrator, elegant, warm and clear",
          "Deep in the heart of the rainforest, a quiet drama unfolds, one that has played out for "
          "millions of years."),
    Style("podcaster", "播客主持", "Podcaster", "english", "m", "english",
          "A relaxed American man in his thirties, podcast host, warm, thoughtful and conversational",
          "So here's the thing nobody tells you about starting a business. The hard part isn't the idea. "
          "It's showing up, every single day."),
    Style("cartoon", "卡通角色", "Cartoon", "english", "m", "english",
          "A playful cartoon character voice, squeaky, bouncy and very funny, fast excited speech",
          "Oh boy, oh boy! Did somebody say pizza? Because I am absolutely, positively starving!"),
)
