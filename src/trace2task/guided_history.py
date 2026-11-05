"""Replay only delivered native actions; keep v6's last two history images."""
import base64
import io

from trace2task.gui_conversation import GuiConversation

MAX_GUIDED_HISTORY = 120


class GuidedConversation(GuiConversation):
    guided_replay = True

    def discard_oldest_image(self):
        # The selected v6 evidence window is fixed. Capacity failure must be
        # explicit instead of silently changing the tested history policy.
        return None


class GuidedTaskConversation(GuiConversation):
    confirmed_history_only = True

    def __init__(self, identity, model, prompt_profile, guided_knowledge=''):
        instruction = ('Execute the current subgoal using the latest screenshot. Previous turns contain confirmed actions. '
                       'Follow the current notices. Honor restrictions and apply conditional tips when relevant. '
                       'A new subgoal replaces earlier notices.')
        super().__init__(identity, model, instruction + guided_knowledge, None, prompt_profile)


def guided_conversation(identity, model, task, history, prompt_profile=None, *, images_supported=True):
    conversation = GuidedConversation(identity, model, task, None, prompt_profile)
    append_guided_history(conversation, history, images_supported=images_supported, maximum=MAX_GUIDED_HISTORY)
    return conversation


def append_guided_history(conversation, history, *, images_supported, maximum=500):
    from PIL import Image

    if not isinstance(history, list) or len(history) > maximum:
        raise ValueError(f'Guided history must contain at most {maximum} delivered actions')
    entries = []
    for index, item in enumerate(history):
        if (not isinstance(item, dict) or set(item) not in ({'raw_output'}, {'raw_output', 'image'})
                or not isinstance(item.get('raw_output'), str) or not item['raw_output'].strip()
                or len(item['raw_output']) > 32768):
            raise ValueError('Invalid delivered native action history')
        pictures = []
        if 'image' in item:
            if index < len(history) - 2 or not isinstance(item['image'], str) or len(item['image']) > 16_000_000:
                raise ValueError('Only the last two delivered actions may carry images')
            picture = Image.open(io.BytesIO(base64.b64decode(item['image'], validate=True)))
            if picture.width * picture.height > 32_000_000:
                raise ValueError('History image too large')
            if images_supported:
                pictures.append(picture.convert('RGB'))
        content = ([{'type': 'image'}] if pictures else
                   [{'type': 'text', 'text': '[Historical screenshot omitted.]'}])
        entries.append((content, item['raw_output'], pictures))
    for entry in entries:
        conversation.commit(*entry)
