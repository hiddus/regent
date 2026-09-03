# -*- coding: utf-8 -*-
import zipfile
from xml.sax.saxutils import escape

OUT = "C:/regent/deliverables/草莓慧联AI玩具算力出海_政府汇报演讲稿.docx"

def run(text, bold, size, font):
    return ('<w:r><w:rPr>'
            '<w:rFonts w:ascii="Times New Roman" w:eastAsia="%s" w:hAnsi="Times New Roman"/>'
            '<w:b w:val="%s"/><w:sz w:val="%d"/></w:rPr>'
            '<w:t xml:space="preserve">%s</w:t></w:r>') % (
        font, "true" if bold else "false", size, escape(text))

def para(text, style):
    align = None; bold = False; size = 28; font = "仿宋"; indent = False
    if style == 'title':
        align = 'center'; bold = True; size = 44; font = "宋体"
    elif style == 'h1':
        bold = True; size = 32; font = "黑体"
    elif style == 'body':
        indent = True
    elif style == 'plain':
        pass
    elif style == 'right':
        align = 'right'
    elif style == 'center':
        align = 'center'
    ppr = '<w:pPr>'
    if indent:
        ppr += '<w:ind w:firstLine="480"/>'
    if align:
        ppr += '<w:jc w:val="%s"/>' % align
    ppr += '<w:spacing w:line="360" w:lineRule="auto"/></w:pPr>'
    if text == '':
        return '<w:p>%s</w:p>' % ppr
    return '<w:p>%s%s</w:p>' % (ppr, run(text, bold, size, font))

paras = [
    ("关于汕头草莓慧联AI玩具算力出海项目有关情况的汇报", "title"),
    ("", "plain"),
    ("尊敬的各位领导：", "plain"),
    ("大家好！现将汕头草莓慧联有限公司AI玩具算力出海项目的有关情况，向各位领导作简要汇报。", "body"),
    ("", "plain"),
    ("一、企业定位与项目背景", "h1"),
    ("汕头草莓慧联有限公司是一家全栈AI基础设施专业服务商，专注于AI原生设备的“机芯+智能体底座+出海合规+全球网络冗备”一体化解决方案，致力于为传统玩具厂商提供从算力部署、智能体集成到全球合规出海的全链路赋能，让每一个AI玩具都拥有“永远在线的灵魂”。", "body"),
    ("当前，硬件行业的规则正在被重写。过去十年我们解决的是设备“在线”问题；而在AI原生硬件时代，核心挑战已升级为“如何让设备聪明和进化”。草莓慧联正是面向这一历史性转变而生的。", "body"),
    ("", "plain"),
    ("二、抢抓AI与玩具产业交汇的黄金机遇", "h1"),
    ("从行业趋势看，全球AI市场规模2026年预计突破5000亿美元，训练算力每3.4个月翻一番，推理需求爆发式增长；端侧AI芯片成本下降60%，为万物智能奠定基础。", "body"),
    ("与此同时，全球玩具市场规模达3000亿美元，中国占出口70%以上，AI玩具赛道年增速超过40%，东南亚、中东市场需求快速爆发。技术成熟、产业转型、政策红利三者交汇，为AI玩具产业落地汕头打开了难得的黄金窗口期。", "body"),
    ("", "plain"),
    ("三、依托汕头独特优势，构筑最佳产业土壤", "h1"),
    ("我们选择扎根汕头，源于这里独一无二的三重优势叠加。一是区位枢纽优势：汕头是国家政企专网节点城市、广东移动八大核心数据中心之一，拥有SJC国际海缆出口权益超4T，一跳出海至新日方向，光缆直达新加坡，时延仅3.2毫秒，可直达全球7个海外数据中心。", "body"),
    ("二是产业集群优势：汕头澄海是全球玩具礼品生产基地，年产值超500亿元，具备从设计、模具、生产到出口的完整供应链，出海渠道成熟。", "body"),
    ("三是算力与制度优势：汕头拥有粤东规模最大的数据中心（167亩、投资30亿、机架3万），是省内唯一同时具备AIDC、国际海缆、零碳园区三重优势的区域；依托华侨试验区“来数加工”试点这一“数字保税区”，可为词元出海提供合规底座，26年底算力将超250Pflops，日产token能力达50亿以上。", "body"),
    ("", "plain"),
    ("四、核心解决方案与产品体系", "h1"),
    ("针对传统玩具厂商“缺知识库、缺智能体支撑、算力部署调度效率低”等痛点，我们提供软硬一体解决方案。在连接侧，以4G/5G eSIM通讯底座实现全场景、零断连，告别“AI智障”；在合规侧，提供AI硬件出海隐私认证与端到端数据隐私安全屏障；在智能侧，集成类脑芯片与通用算力，支持品牌LOGO、唤醒词、音色、故事内容定制，大幅缩短上市周期。", "body"),
    ("目前，我们已形成“小闻AI萌宠诊疗仪”（中国首款量产嵌入式AI对话式智能终端，已服务超2000家线下宠物诊所）和“AI智能陪伴玩偶”（4G独立联网、灵动表情屏、拟人化动作）等成熟产品，并于2026年6月登上央视《朝闻天下》。", "body"),
    ("", "plain"),
    ("五、自主可控能力与知识产权", "h1"),
    ("草莓慧联起步早、积累深。目前已获发明专利等81项（含3项安全芯片专利）、计算机软件著作权33项，完成包括高通、中兴、联发科、展讯、锐迪科等主流通讯基带芯片的全面适配，具备从芯片到平台的自主可控能力。", "body"),
    ("", "plain"),
    ("六、经济社会效益与下一步打算", "h1"),
    ("本项目推动传统劳动密集的玩具产业向AI驱动的效率提升型转型，有利于稳外贸、稳就业、培育新质生产力；同时依托“来数加工”合规底座，打造数据跨境流动的安全可信标杆，服务国家数字出海战略。", "body"),
    ("下一步，草莓慧联将：一是扩大AI机芯与智能体平台产能，服务更多出海企业；二是深化与汕头算力园区协同，共建粤东AI玩具算力高地；三是拓展东南亚、中东等海外市场，让“中国智造”的AI玩具走向全球。在此，恳请各位领导在产业政策、算力资源对接、应用场景开放等方面给予支持指导。", "body"),
    ("", "plain"),
    ("以上汇报，不当之处，请各位领导批评指正。", "body"),
    ("谢谢大家！", "center"),
    ("", "plain"),
    ("汕头草莓慧联有限公司", "right"),
    ("2026年8月", "right"),
]

body_xml = "".join(para(t, s) for t, s in paras)
document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>' + body_xml + '<w:sectPr/></w:body></w:document>')

content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                 '<Default Extension="xml" ContentType="application/xml"/>'
                 '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                 '</Types>')

rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", content_types)
    z.writestr("_rels/.rels", rels)
    z.writestr("word/document.xml", document)

import os
print("saved:", OUT, "size KB:", round(os.path.getsize(OUT)/1024, 1))
