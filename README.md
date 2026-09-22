# plugin.py

class PluginSectionConfig(PluginConfigBase):
  有什么？
  1. [plugin]
  enabled = true
  config_version = "1.0.0"
  2. [scpoe]
  3. [CoverCache]
  4. [Permission]

recommend_chinese_vocaloid
推荐一首中v，发布中v的b站链接
  群友回复r"(?i)^/(?:中v推荐|随机中v)\s*$"
  选择
  1. 我不需要知道群友的信息，直接回复即可
  2. 我需要知道群友的信息，防止恶意群友的轰炸

  在库中随机选择一首，注意若抽中最近std::min(10, total_number - 1)首被随机到的，需要重新随机
  随机到合法的链接后，发布

  格式：“
    1. 回复群友“中v推荐”的信息。
    2. 封面【若有缓存图，发，否则根据记录的超链接获取】
    3. ID：标题
    4. 播放量，点赞量
    5. 可点进去的超链接
  ”

  新添功能：‘中v随机 + 数字’ 作为 ID编号特定搜寻，若有，则可特定发布，若没有，需要输出特定消息报错

update_chinese_vocaloid
群友上传中v链接到库，参数 <B站链接>
  1. 语句是否合法，超链接是否合法。
  2. 若合法，存库中。

to_delete_chinese_vocaloid
  1. 判断是否合法

help_chinese_vocaloid
  直接回复：
  给出一些指令 + 功能