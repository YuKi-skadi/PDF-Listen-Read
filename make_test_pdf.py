from pathlib import Path

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


OUTPUT = Path(__file__).with_name("test-300字.pdf")
FONT_PATH = Path(r"C:\Windows\Fonts\msyh.ttc")

text = (
    "这是一个用于验证论文库导入、文本提取和朗读流程的测试文档。本文围绕数字化阅读展开一个简短示例。"
    "随着电子资料不断增加，研究者需要一个能够集中保存论文、整理正文、记录阅读状态并生成语音的工具。"
    "在实际使用中，PDF 文件经常包含封面、目录、作者信息、页眉页脚和参考文献，这些内容会影响正文阅读的连续性。"
    "理想的处理流程应该先保留原始文件，再对识别出的文本进行清理，修复错误空格，恢复段落结构，并允许用户手动修改。"
    "当文本确认无误后，系统可以按照段落生成音频，并在播放时同步高亮当前段落。这样用户既可以阅读原文，也可以在通勤或休息时通过声音学习。"
    "未来还可以把论文内容接入知识库，通过向量检索和重排序快速定位相关观点，帮助用户比较不同论文的研究方法、结论与局限。"
    "本测试文件不代表真实研究成果，仅用于检查导入、分类、文本显示、音频入口和删除级联等基础功能。"
)

pdfmetrics.registerFont(TTFont("MicrosoftYaHei", str(FONT_PATH), subfontIndex=0))
styles = getSampleStyleSheet()
title_style = ParagraphStyle(
    "TestTitle", parent=styles["Title"], fontName="MicrosoftYaHei", fontSize=18,
    leading=28, alignment=TA_CENTER, spaceAfter=12,
)
body_style = ParagraphStyle(
    "TestBody", parent=styles["BodyText"], fontName="MicrosoftYaHei", fontSize=12,
    leading=22, firstLineIndent=24, wordWrap="CJK", spaceAfter=8,
)
meta_style = ParagraphStyle(
    "TestMeta", parent=styles["Normal"], fontName="MicrosoftYaHei", fontSize=9,
    leading=16, textColor="#666666", alignment=TA_CENTER, spaceAfter=18,
)

doc = SimpleDocTemplate(
    str(OUTPUT), pagesize=A4, rightMargin=22 * mm, leftMargin=22 * mm,
    topMargin=20 * mm, bottomMargin=20 * mm,
)
story = [
    Paragraph("论文库导入流程测试文档", title_style),
    Paragraph("用于 PDF 导入、文本识别和语音阅读功能验收", meta_style),
    Paragraph(text, body_style),
]
doc.build(story)
print(f"created={OUTPUT}")
print(f"characters={len(text)}")
