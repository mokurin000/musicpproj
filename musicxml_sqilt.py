from music21 import converter, stream


def split_musicxml_by_parts(input_file, output_folder):
    """
    将一个包含多个声部的 MusicXML 文件拆分成多个单独的 MusicXML 文件。

    参数:
        input_file: 输入的 MusicXML 文件路径
        output_folder: 输出文件夹路径
    """
    # 加载 MusicXML 文件
    score = converter.parse(input_file)

    # 获取所有声部
    parts = score.parts

    # 遍历每个声部并保存为单独的 MusicXML 文件
    for i, part in enumerate(parts):
        # 创建一个新的乐谱对象
        new_score = stream.Score()
        new_score.append(part)

        # 构造输出文件名
        output_file = f"{output_folder}/part_{i + 1}.xml"

        # 保存为 MusicXML 文件
        new_score.write('xml', fp=output_file)
        print(f"已保存：{output_file}")


# 示例用法
