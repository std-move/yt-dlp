"""
Download one audio track per language (the ``all_audio_languages`` option).

Everything specific to this feature lives here so that the hooks in
``YoutubeDL``, ``FFmpegMergerPP`` and ``FFmpegFD`` stay one-liners.

How it works (see ``expand_selected_formats``):

1. The regular format selector picks the formats as usual.
2. For every selected entry that consists of video plus exactly one audio-only
   track, the *same* selector is evaluated once more per additional language,
   with the audio-only tracks of all other languages hidden. Whatever audio it
   picks then is added as an extra audio stream. If it picks nothing for a
   language (e.g. because of a filter like ``ba[ext=m4a]`` or ``ba[language=de]``),
   that language is skipped, so the selector keeps its meaning.
3. The audio streams are ordered by ``language_preference`` (stable sort), so a
   track the site marks as default/original comes first even if it is not the
   best one. If no single track has the highest ``language_preference``, the
   track in the ``default_audio_language`` (Czech by default) comes first
   instead. The merge itself is done by the regular ``mergeall`` selector.
"""

import re

from .utils import ISO639Utils, float_or_none, orderedSet

# Extractors (matched case-insensitively by prefix of `extractor_key`) for which
# the feature is off unless `all_audio_languages` is explicitly set to True
DISABLED_BY_DEFAULT_EXTRACTORS = ('youtube',)

# Used when the `default_audio_language` param is None
DEFAULT_AUDIO_LANGUAGE = 'cs'

# Language codes that do not identify an actual language
_NO_LANGUAGE = frozenset(('und', 'zxx', 'mul', 'mis'))

# ISO 639-2/B codes that differ from their ISO 639-2/T counterpart
_ISO639_2B_TO_T = {
    'alb': 'sqi', 'arm': 'hye', 'baq': 'eus', 'bur': 'mya', 'chi': 'zho', 'cze': 'ces', 'dut': 'nld',
    'fre': 'fra', 'geo': 'kat', 'ger': 'deu', 'gre': 'ell', 'ice': 'isl', 'mac': 'mkd', 'mao': 'mri',
    'may': 'msa', 'per': 'fas', 'rum': 'ron', 'slo': 'slk', 'tib': 'bod', 'wel': 'cym',
}


def language_key(fmt):
    """
    Normalized primary language of a format as ISO 639-2/T code, e.g. 'en-US' -> 'eng', 'ger' -> 'deu'.
    Returns None if the format has no usable language tag.
    """
    lang = fmt.get('language')
    if not isinstance(lang, str):
        return None
    primary = re.split(r'[-_]', lang.strip().lower(), maxsplit=1)[0]
    if not primary or primary in _NO_LANGUAGE:
        return None
    if len(primary) == 2:
        primary = ISO639Utils.short2long(primary) or primary
    return _ISO639_2B_TO_T.get(primary, primary)


def is_audio_only(fmt):
    return fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none'


def _language_preference(fmt):
    return float_or_none(fmt.get('language_preference'), default=-1)


def _format_ids(formats):
    return [f.get('format_id') for f in formats]


def _default_audio_language(ydl):
    """Language key of the `default_audio_language` param, None if disabled"""
    lang = ydl.params.get('default_audio_language')
    if lang is None:
        lang = DEFAULT_AUDIO_LANGUAGE
    return language_key({'language': lang})


def _order_audio_tracks(ydl, audio_tracks):
    # Stable: the track with the highest language_preference (site default/original) goes first,
    # otherwise the order given by the format sorting is kept
    tracks = sorted(audio_tracks, key=lambda f: -_language_preference(f))
    if len(tracks) < 2 or _language_preference(tracks[0]) != _language_preference(tracks[1]):
        return tracks

    # No single track is marked as default by the site
    default_language = _default_audio_language(ydl)
    if default_language:
        idx = next((i for i, f in enumerate(tracks) if language_key(f) == default_language), None)
        if idx is not None:
            tracks.insert(0, tracks.pop(idx))
    return tracks


def is_enabled(ydl, info_dict):
    """Whether one audio track per language should be downloaded for this video"""
    enabled = ydl.params.get('all_audio_languages')
    if enabled is None:
        extractor = (info_dict.get('extractor_key') or info_dict.get('extractor') or '').lower()
        enabled = not extractor.startswith(DISABLED_BY_DEFAULT_EXTRACTORS)
    if not enabled:
        return False

    # Same situations in which YoutubeDL._default_format_spec avoids merging
    if (ydl.params.get('outtmpl') or {}).get('default') == '-':
        return False
    if info_dict.get('is_live') and not ydl.params.get('live_from_start'):
        return False
    if ydl.params.get('allow_unplayable_formats'):
        return False

    # Deferred import: postprocessor.ffmpeg imports this module
    from .postprocessor.ffmpeg import FFmpegMergerPP
    merger = FFmpegMergerPP(ydl)
    return bool(merger.available and merger.can_merge())


def _merge_formats(ydl, parts):
    """Merge `parts` (in the given order) with the regular merge logic of the format selector"""
    # `mergeall` merges from the last format to the first one
    selector = ydl.build_format_selector('mergeall', allow_multiple_audio_streams=True)
    merged = ydl._select_formats(list(reversed(parts)), selector)
    return merged[0] if merged else None


def _expand_format(ydl, formats, format_selector, selected, index, languages):
    fmt = selected[index]
    parts = fmt.get('requested_formats') or [fmt]
    video_parts = [f for f in parts if f.get('vcodec') != 'none']
    audio_parts = [f for f in parts if f.get('acodec') != 'none']
    # Nothing to do for muxed or audio-only downloads, or if multiple audio streams were selected already
    if not video_parts or len(audio_parts) != 1 or not is_audio_only(audio_parts[0]):
        return None

    primary = audio_parts[0]
    primary_language = language_key(primary)
    video_ids = _format_ids(video_parts)
    audio_tracks = [primary]
    for language in languages:
        if language == primary_language:
            continue
        # Hide the audio-only tracks of all other languages and ask the same selector again
        restricted = [f for f in formats if not is_audio_only(f) or language_key(f) == language]
        candidates = ydl._select_formats(restricted, format_selector)
        if len(candidates) != len(selected):
            continue
        candidate = candidates[index]
        candidate_parts = candidate.get('requested_formats') or [candidate]
        candidate_video = [f for f in candidate_parts if f.get('vcodec') != 'none']
        candidate_audio = [f for f in candidate_parts if f.get('acodec') != 'none']
        if (_format_ids(candidate_video) != video_ids
                or len(candidate_audio) != 1
                or not is_audio_only(candidate_audio[0])
                or language_key(candidate_audio[0]) != language):
            continue
        audio_tracks.append(candidate_audio[0])

    if len(audio_tracks) == 1:
        return None
    return _merge_formats(ydl, [*video_parts, *_order_audio_tracks(ydl, audio_tracks)])


def expand_selected_formats(ydl, info_dict, formats, format_selector, selected):
    """
    Add one audio track for every additional language to the selected formats.

    @param formats          All formats of the video, sorted worst to best
    @param format_selector  The selector that produced `selected` (see YoutubeDL.build_format_selector)
    @param selected         The formats returned by the selector
    @returns                `selected`, with entries replaced by multi-audio merges where applicable
    """
    if not selected:
        return selected

    formats = list(formats)
    # Languages in order of their best audio track
    languages = orderedSet(filter(None, (language_key(f) for f in reversed(formats) if is_audio_only(f))))
    if not languages or not is_enabled(ydl, info_dict):
        return selected

    expanded = []
    for index, fmt in enumerate(selected):
        merged = _expand_format(ydl, formats, format_selector, selected, index, languages)
        if merged is None:
            expanded.append(fmt)
            continue
        audio_langs = [language_key(f) or 'unknown' for f in merged['requested_formats'] if is_audio_only(f)]
        ydl.to_screen(f'[info] {info_dict["id"]}: Selected audio tracks for languages: {", ".join(audio_langs)}')
        expanded.append(merged)
    return expanded


def ffmpeg_audio_stream_args(requested_formats):
    """
    ffmpeg output options for a merge with more than one audio stream:
    tag every audio stream with its language and mark only the first one (and the video) as default.
    Other disposition flags of the inputs are kept
    """
    # Muxed inputs would make the output audio stream indices ambiguous
    if any('none' not in (f.get('vcodec'), f.get('acodec')) for f in requested_formats):
        return []
    audio_formats = [f for f in requested_formats if f.get('acodec') != 'none']
    if len(audio_formats) < 2:
        return []

    args = []
    # Otherwise the video is written as non-default if its input has no default flag (e.g. MPEG-TS from HLS)
    if any(f.get('vcodec') != 'none' for f in requested_formats):
        args += ['-disposition:v:0', '+default']
    for idx, fmt in enumerate(audio_formats):
        lang = language_key(fmt)
        if lang and re.fullmatch(r'[a-z]{3}', lang):
            args += [f'-metadata:s:a:{idx}', f'language={lang}']
        args += [f'-disposition:a:{idx}', '+default' if idx == 0 else '-default']
    return args
