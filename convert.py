from music21 import converter, midi

def musicxml_to_midi(musicxml_file, midi_file):
    """
    将MusicXML文件转换为MIDI文件

    参数:
        musicxml_file (str): 输入的MusicXML文件路径
        midi_file (str): 输出的MIDI文件路径
    """
    # 解析MusicXML文件
    score = converter.parse(musicxml_file)

    # 转换为MIDI文件
    mf = midi.translate.streamToMidiFile(score)
    mf.open(midi_file, 'wb')
    mf.write()
    mf.close()
    print(f"成功将 {musicxml_file} 转换为 {midi_file}")


# 使用示例
if __name__ == "__main__":
    input_file ="part_1.musicxml"  # 替换为你的MusicXML文件
    output_file = "part_1.mid"  # 输出的MIDI文件名

    musicxml_to_midi(input_file, output_file)