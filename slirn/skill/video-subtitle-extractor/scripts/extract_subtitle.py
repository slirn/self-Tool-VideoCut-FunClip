#!/usr/bin/env python3
# -*- encoding: utf-8 -*-
"""
从视频文件提取 SRT 字幕。
支持多个视频文件批量处理，输出独立 SRT 文件。

用法:
    python extract_subtitle.py --input video.mp4 --output video.srt
    python extract_subtitle.py --input video.mp4 --output video.srt --model sensevoice
    python extract_subtitle.py --input video1.mp4 video2.mp4 --output-dir ./subs
"""

import argparse
import os
import sys
import logging
from datetime import datetime

# 确保 funclip 模块在路径中（支持从任意目录运行）
_script_dir = os.path.dirname(os.path.abspath(__file__))
_funiclip_root = os.path.join(_script_dir, '..', '..', '..', '..')
_funiclip_root = os.path.normpath(_funiclip_root)
_funclip_pkg_dir = os.path.join(_funiclip_root, 'funclip')

for path in (_funiclip_root, _funclip_pkg_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

from funclip.videoclipper import VideoClipper
from funasr import AutoModel


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def build_model(model_name='paraformer', lang='zh', use_spk=False):
    """初始化 FunASR AutoModel"""
    if model_name == 'fun-asr-nano':
        kwargs = dict(
            model="FunAudioLLM/Fun-ASR-Nano-2512",
            trust_remote_code=True,
            remote_code="./model.py",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            hub="hf",
        )
        if use_spk:
            kwargs["spk_model"] = "cam++"
        return AutoModel(**kwargs)
    elif model_name == 'sensevoice':
        kwargs = dict(
            model="iic/SenseVoiceSmall",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
        )
        if use_spk:
            kwargs["spk_model"] = "cam++"
        return AutoModel(**kwargs)
    else:  # paraformer 默认
        if lang == 'en':
            kwargs = dict(
                model="iic/speech_paraformer_asr-en-16k-vocab4199-pytorch",
                vad_model="damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                punc_model="damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
            )
            if use_spk:
                kwargs["spk_model"] = "damo/speech_campplus_sv_zh-cn-16k-common"
            return AutoModel(**kwargs)
        else:
            kwargs = dict(
                model="iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                vad_model="damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                punc_model="damo/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
            )
            if use_spk:
                kwargs["spk_model"] = "damo/speech_campplus_sv_zh-cn-16k-common"
            return AutoModel(**kwargs)


def normalize_srt(srt_text):
    """
    规范化 SRT 格式：
    - 替换所有 \\r\\n、\\r、\\n 为统一换行符
    - 字幕块之间只留一个空行
    - 字幕块内部（序号、时间戳、文本）不空行
    - 不添加尾部多余空行
    """
    import re

    # 统一换行符
    text = srt_text.replace('\r\n', '\n').replace('\r', '\n')

    # 按行分割，收集字幕块
    lines = text.split('\n')
    blocks = []
    current_block_lines = []

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            # 空行：结束当前字幕块
            if current_block_lines:
                blocks.append('\n'.join(current_block_lines))
                current_block_lines = []
            continue
        if re.match(r'^\d+$', line_stripped):
            # 序号行：开始新字幕块
            if current_block_lines:
                blocks.append('\n'.join(current_block_lines))
                current_block_lines = []
            current_block_lines.append(line_stripped)
        else:
            current_block_lines.append(line_stripped)

    if current_block_lines:
        blocks.append('\n'.join(current_block_lines))

    # 字幕块之间单空行连接，无尾部空行
    return '\n\n'.join(blocks)


def extract_subtitle(video_path, output_path, model_name='paraformer', lang='zh', hotwords='', sd_switch='no'):
    """
    从单个视频提取字幕

    Args:
        video_path: 视频文件路径
        output_path: 输出 SRT 文件路径
        model_name: ASR 模型名 ('paraformer' | 'fun-asr-nano' | 'sensevoice')
        lang: 语言 ('zh' | 'en')
        hotwords: 热词字符串，空格分隔
        sd_switch: 是否区分说话人 ('yes' | 'no')
    """
    logger.info(f"Initializing {model_name} model...")
    funasr_model = build_model(model_name, lang, use_spk=(sd_switch == 'yes'))

    clipper = VideoClipper(funasr_model)
    clipper.lang = lang

    logger.info(f"Processing: {video_path}")
    res_text, res_srt, state = clipper.video_recog(
        video_filename=video_path,
        sd_switch=sd_switch,
        hotwords=hotwords,
        output_dir=os.path.dirname(output_path) or None,
    )

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    # 格式化 SRT：统一换行符，单字幕块内不空行，块之间单空行
    formatted_srt = normalize_srt(res_srt)

    with open(output_path, 'w', encoding='utf-8-sig') as f:
        f.write(formatted_srt)

    logger.info(f"SRT saved to: {output_path}")
    return output_path


def batch_extract(video_paths, output_dir, model_name='paraformer', lang='zh', hotwords='', sd_switch='no'):
    """
    批量从多个视频提取字幕
    每个视频输出同名 .srt 文件到 output_dir
    """
    os.makedirs(output_dir, exist_ok=True)

    funasr_model = build_model(model_name, lang, use_spk=(sd_switch == 'yes'))
    clipper = VideoClipper(funasr_model)
    clipper.lang = lang

    for video_path in video_paths:
        basename = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(output_dir, f"{basename}.srt")

        logger.info(f"Processing: {video_path} -> {output_path}")
        try:
            res_text, res_srt, state = clipper.video_recog(
                video_filename=video_path,
                sd_switch=sd_switch,
                hotwords=hotwords,
                output_dir=output_dir,
            )
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(res_srt)
            logger.info(f"Saved: {output_path}")
        except Exception as e:
            logger.error(f"Failed to process {video_path}: {e}")
            continue

    logger.info(f"Batch extraction complete. Output dir: {output_dir}")


def get_parser():
    parser = argparse.ArgumentParser(
        description='从视频文件提取 SRT 字幕',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        nargs='+',
        required=True,
        help='输入视频文件路径，支持单个或多个文件'
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='单个视频时指定输出 SRT 路径（默认与视频同名）'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='批量处理时指定输出目录'
    )
    parser.add_argument(
        '--model', '-m',
        type=str,
        default='paraformer',
        choices=['paraformer', 'fun-asr-nano', 'sensevoice'],
        help='ASR 模型（默认 paraformer）'
    )
    parser.add_argument(
        '--lang', '-l',
        type=str,
        default='zh',
        choices=['zh', 'en'],
        help='语言（默认中文）'
    )
    parser.add_argument(
        '--hotwords',
        type=str,
        default='',
        help='热词，多个用空格分隔（仅支持中文）'
    )
    parser.add_argument(
        '--sd-switch',
        type=str,
        default='no',
        choices=['yes', 'no'],
        help='是否区分说话人（默认否）'
    )
    return parser


def main():
    args = get_parser().parse_args()

    # 单个视频，指定输出路径
    if len(args.input) == 1 and args.output:
        extract_subtitle(
            video_path=args.input[0],
            output_path=args.output,
            model_name=args.model,
            lang=args.lang,
            hotwords=args.hotwords,
            sd_switch=args.sd_switch,
        )
    # 单个视频，输出路径从输入推导
    elif len(args.input) == 1:
        video_path = args.input[0]
        if args.output_dir:
            basename = os.path.splitext(os.path.basename(video_path))[0]
            output_path = os.path.join(args.output_dir, f"{basename}.srt")
        else:
            output_path = os.path.splitext(video_path)[0] + '.srt'
        extract_subtitle(
            video_path=video_path,
            output_path=output_path,
            model_name=args.model,
            lang=args.lang,
            hotwords=args.hotwords,
            sd_switch=args.sd_switch,
        )
    # 多个视频
    else:
        if args.output_dir is None:
            raise ValueError("批量处理时必须指定 --output-dir")
        batch_extract(
            video_paths=args.input,
            output_dir=args.output_dir,
            model_name=args.model,
            lang=args.lang,
            hotwords=args.hotwords,
            sd_switch=args.sd_switch,
        )


if __name__ == '__main__':
    main()
