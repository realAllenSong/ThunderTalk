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


# Explainer styles (added for 1.6.3): designed to match the measured pitch,
# pace and pitch range of popular Chinese explainer videos, without cloning
# anyone. Kept here so the shipped clips' descriptions stay documented.
EXPLAINER_STYLES: tuple[Style, ...] = (
    Style("explainer", "科普讲述", "Explainer", "chinese", "m", "narration",
          "三十岁左右的男性，音色温暖、音高中等不低沉，科普视频旁白，像跟朋友讲故事一样娓娓道来，语速偏慢，自然停顿，轻松口语化，录音棚干净录音",
          "你可能没想过，每天多喝一杯奶茶，身体会发生什么变化。第一周，你几乎感觉不到任何不同。可到了第二个月，问题就开始慢慢冒出来了。"),
    Style("business-explainer", "商业解读", "Business explainer", "chinese", "f", "narration",
          "二十多岁的年轻女性，声音清亮干脆，财经商业科普视频解说，语速偏快、节奏利落，自信有条理，像在跟观众分析一个故事，不是播新闻，干净录音",
          "这家公司十年前还只是一家小酒店，如今却成了行业里最赚钱的玩家之一。它到底做对了什么？答案，可能就藏在一个不起眼的细节里。"),
)


# November 2026 round: what the user asked for after 1.17.0. Chinese only (the
# English voices were fine): short-video staples, voices with a premium feel,
# and delivery styles of creators whose videos do well. For the last group the
# pace, pauses, median pitch and pitch range of ten public videos were measured
# on vocals separated from the music (2026-11-21, ~/Library/Caches/ThunderTalk-bench/
# voices-2026-11/refs/measure.json) and written into the descriptions as words.
# Nobody is cloned or named: every clip is designed from the description alone.
# Lines follow the spoken-Chinese rules in the writing-explainer-copy skill
# (whole sentences, two-character words, a particle where a person would use one).
STYLES_2026_11: tuple[Style, ...] = (
    # ── creator delivery styles (measured) ──────────────────────────────
    Style("calm-explainer", "冷静讲解", "Calm explainer", "chinese", "m", "creator",   # 157 Hz, 4.1 字/秒, 14 st
          "三十岁左右的男性，音高中等，声音干净克制，科普短片的解说，语速平稳不快，咬字清楚，冷静理性，不渲染情绪，"
          "像在把一件复杂的事情讲明白，录音棚干净录音",
          "一件短袖的成本到底是多少呢？我们把它拆开来看。面料、人工、运输，每一项加在一起，其实比你想象的要低得多。"),
    Style("fast-finance", "快节奏财经", "Fast finance", "chinese", "f", "creator",     # 190 Hz, 6.1 字/秒, few pauses
          "二十七八岁的女性，音高中等偏低，声音干净有颗粒感，财经科普视频，语速很快但是每个字都清楚，"
          "像跟朋友聊天一样把事情讲透，带一点笑意，不端着，不是播音腔",
          "这个游戏你可能没有玩过，但是你一定听说过。它的背后是一场上千亿的豪赌，今天咱们就来聊一聊，这些钱到底是怎么花出去的。"),
    Style("deadpan-rant", "冷面吐槽", "Deadpan rant", "chinese", "m", "creator",       # 113 Hz, 6.5 字/秒, 6.6 st, no pauses
          "三十多岁的男性，嗓音偏低，语速非常快，几乎不换气，语调很平，面无表情地吐槽，冷幽默，越平静越好笑",
          "这位老师的操作，只能说非常魔幻。骂他的人可以绕地球整整一圈，但是你猜怎么着，他一点儿都不慌，甚至还想再来一次。"),
    Style("history-yarn", "历史评书", "History yarn", "chinese", "m", "creator",       # 163 Hz, 4.6 字/秒, 25 pauses/min
          "三十多岁的男性，音高中等，讲历史故事，带一点说书人的腔调和幽默感，抑扬顿挫很明显，停顿多，娓娓道来又带着一点坏笑",
          "话说这位老爷子，年轻的时候可不是什么省油的灯。那一年，他只带了三个人，就干成了一件谁都不敢想的大事。"),
    Style("culture-talk", "人文讲述", "Culture talk", "chinese", "f", "creator",       # 214 Hz, 3.7 字/秒, 23 pauses/min
          "三十岁左右的女性，声音温暖有感染力，讲诗词和人文故事，语速偏慢，停顿多，带着感叹和笑意，像在跟你分享她特别喜欢的东西",
          "你有没有发现啊，苏东坡的词，读着读着，心里就松下来了。他好像什么都经历过，可是他从来都不抱怨。"),
    Style("travel-essay", "旅行文案", "Travel essay", "chinese", "f", "creator",       # 257 Hz, slow, pauses 0.8 s
          "二十多岁的年轻女性，声音清澈干净，旅行短片的独白，语速很慢，句子之间停顿很长，克制的深情，有画面感，不煽情",
          "我也不知道，那天晚上他为什么睡不着。很多年以后，我站在同一条河边，才忽然明白，有些句子，是要等你走到那里，才看得懂的。"),
    Style("lecture-wit", "幽默讲堂", "Witty lecturer", "chinese", "m", "creator",      # 171 Hz, fast, 17.5 st
          "四十多岁的男老师，音高中等偏高，在课堂上讲案例，语速快，语调起伏很大，自带幽默，讲到好笑的地方自己也在忍着笑，很亲切",
          "张三这个人呢，他就想，哎，我凑够三个人，是不是就可以省下六块钱了？你看，人为了省这六块钱，能想出多少办法来。"),
    Style("deep-report", "深度解说", "Deep report", "chinese", "m", "creator",         # 5.4 字/秒, steady, 9 st
          "三十多岁的男性，声音低沉厚实，纪实类长视频的解说，语速偏快而且很稳，严肃克制，有分量，像在读一篇深度报道",
          "二十年的时间，和无数种诈骗手段死磕之后，这套系统终于给整个金融体系，装上了一道刹车。"),
    # ── short-video staples ─────────────────────────────────────────────
    Style("late-night", "深夜电台", "Late-night radio", "chinese", "m", "shortvideo",
          "三十多岁的男性，嗓音低沉温柔，带着气声，深夜情感电台的主播，语速很慢，贴着话筒轻声说话，很治愈",
          "这么晚了，还没有睡吧。没关系，今天过得不好也没有关系。你已经很努力了，剩下的事情，就交给明天吧。"),
    Style("healing", "治愈女声", "Healing voice", "chinese", "f", "shortvideo",
          "二十多岁的女性，声音轻柔温暖，情感类短视频的旁白，语速慢，像在安慰一个朋友，真诚不做作",
          "后来我才明白，不是所有的事情都要有一个结果。有些人，能陪你走一段路，就已经很好了。"),
    Style("motivation", "励志口播", "Motivation", "chinese", "m", "shortvideo",
          "三十岁左右的男性，声音坚定有力量，励志短视频的口播，语速中等，重音清楚，不喊也不吼，沉着地给人打气",
          "你现在觉得难，是因为你正在走上坡路。别停下来，再坚持一下，等到回头看的时候，你会感谢今天没有放弃的自己。"),
    Style("livestream", "带货主播", "Live seller", "chinese", "f", "shortvideo",
          "二十多岁的女主播，在直播间带货，语速快，热情有感染力，声音明亮，节奏感很强，真诚地推荐",
          "姐妹们看过来，这个价格我只说一遍！今天直播间拍一发三，库存真的不多了，喜欢的姐妹直接拍，不要犹豫！"),
    Style("suspense", "悬疑故事", "Suspense", "chinese", "m", "shortvideo",
          "中年男性，嗓音低而且略带沙哑，讲悬疑故事，语速慢，压着声音，句尾留白，让人后背发凉",
          "那天夜里十二点，他听见有人在敲门。可是他明明记得，这栋楼的六层，已经空了整整三年。"),
    Style("foodie", "美食探店", "Foodie", "chinese", "m", "shortvideo",
          "二十多岁的男生，探店博主，声音有活力，带着刚吃到好东西的兴奋，口语化，接地气",
          "朋友们，这家店我真的忍不住要说，一口咬下去，外面是脆的，里面还在流汁，三十块钱吃到撑，谁懂啊！"),
    Style("game-cast", "游戏解说", "Game caster", "chinese", "m", "shortvideo",
          "二十岁出头的男生，游戏解说，语速快，情绪饱满，带着激动和调侃，声音清亮",
          "注意看这一波操作！残血反杀，一打三！兄弟们，这就是为什么我说，这个英雄现在就是版本答案。"),
    Style("audiobook", "有声书", "Audiobook", "chinese", "f", "shortvideo",
          "三十岁左右的女性，有声书演播，声音细腻干净，叙事感强，语速平稳，人物的情绪点到为止",
          "她推开门的时候，雨刚好停了。院子里的桂花落了一地，空气里全是甜的。她忽然想起来，上一次回来，已经是七年前的事情了。"),
    Style("tech-review", "数码评测", "Tech review", "chinese", "m", "shortvideo",
          "二十七八岁的男性，数码评测博主，声音清爽自信，语速中等偏快，逻辑清楚，干净利落，带一点轻松的调侃",
          "先说结论，这台机器我用了两周，优点很明显，缺点也很真实。屏幕和续航，是我觉得它最值得买的两个理由。"),
    # ── premium ─────────────────────────────────────────────────────────
    Style("brand-film-m", "品牌片男声", "Brand film (male)", "chinese", "m", "premium",
          "四十岁左右的男性，声音低沉醇厚有质感，高端品牌宣传片的旁白，语速很慢，字字有分量，留白多，沉稳大气，有电影感",
          "有些东西，时间拿不走。一针，一线，一道弧度。三十年，我们只做一件事情，把它做到最好。"),
    Style("brand-film-f", "品牌片女声", "Brand film (female)", "chinese", "f", "premium",
          "三十多岁的女性，声音优雅从容，略低有磁性，高端品牌广告的旁白，语速慢，克制而有力量，高级感",
          "真正的好，从来不急着被看见。它在细节里，在分寸里，在你每一次不经意的触碰里。"),
    Style("documentary-f", "纪录片女声", "Documentary (female)", "chinese", "f", "premium",
          "四十岁左右的女性，声音温润沉静，人文纪录片的旁白，语速舒缓，娓娓道来，有温度也有阅历",
          "清晨五点，这座小城还没有醒来。巷子口的早点铺，已经亮起了灯。四十年了，老板娘每天都是第一个到的。"),
    Style("podcast-f", "播客女声", "Podcast (female)", "chinese", "f", "premium",
          "三十岁左右的女性，播客主播，声音松弛自然，带一点沙哑的质感，像坐在你对面聊天，语速自然，有思考的停顿，偶尔带着笑",
          "我最近一直在想一个问题啊，就是我们为什么那么害怕无聊。其实你想想，小时候的我们，才是最会跟无聊相处的。"),
    Style("podcast-m", "播客男声", "Podcast (male)", "chinese", "m", "premium",
          "三十多岁的男性，播客主播，声音松弛有磁性，像朋友之间聊天，语速自然，有停顿，真实，不是播音腔",
          "其实吧，我以前也觉得这件事情挺简单的。后来真的自己做了一遍才发现，哎，完全不是那么回事。"),
)

# VOICE_SET=2026-11 makes the candidate tools (design / score / lab page) work on that round alone.
import os as _os  # noqa: E402

if _os.environ.get("VOICE_SET") == "2026-11":
    STYLES = STYLES_2026_11
