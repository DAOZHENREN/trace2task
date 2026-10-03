"""Task-owned GUI dialogue, with explicitly audited historical summaries."""
from copy import deepcopy


class GuiConversation:
    def __init__(self, identity, model, task, experience, prompt_profile=None):
        self.identity, self.model, self.task = identity, model, task
        self.experience = deepcopy(experience)
        self.prompt_profile = deepcopy(prompt_profile)
        # Immutable task text lives outside the compressible observation/reply turns.
        self.initial_user_text = task
        self.evidence_images = []
        self.evidence_image_ids = []
        self.turns = []
        self.step_indices = []
        self.dropped_images = 0
        self.summary = None
        self.summary_version = 0
        self.last_total_tokens = None
        self.total_turns = 0

    def compose(self, system, content, images):
        messages = [{"role": "system", "content": system}]
        if self.summary:
            messages.append({'role': 'user', 'content': self.summary})
        all_images = []
        for user, reply, pictures in self.turns:
            messages.extend([user, {"role": "assistant", "content": reply}])
            all_images.extend(pictures)
        messages.append({"role": "user", "content": content})
        # Merge with the first observation until it is summarized away. After
        # compaction the task remains a separate, verbatim user message.
        seed = [{'type': 'text', 'text': self.initial_user_text}]
        for index, identity in enumerate(self.evidence_image_ids):
            seed.extend([{'type': 'text', 'text': f'Historical demonstration A attachment {index + 1}, frame {identity}. NOT the current screen.'},
                         {'type': 'image'}])
        if self.summary:
            messages.insert(1, {'role': 'user', 'content': seed})
        else:
            messages[1] = deepcopy(messages[1])
            messages[1]['content'] = [*seed, *messages[1]['content']]
        return messages, [*self.evidence_images, *all_images, *images]

    def discard_oldest_image(self):
        """Remove one oldest image and its marker, retaining every text verbatim."""
        for index, (user, _reply, pictures) in enumerate(self.turns):
            if not pictures:
                continue
            marker = next(i for i, part in enumerate(user['content']) if part.get('type') == 'image')
            del user['content'][marker]
            pictures.pop(0)
            if not user['content']:
                user['content'].append({'type': 'text', 'text': '[Historical screenshot omitted.]'})
            self.dropped_images += 1
            return {'turn_index': index, 'step_index': self.step_indices[index],
                    'removed_images': 1, 'removed_text_messages': 0}
        return None

    def commit(self, content, reply, images, step_index=None):
        self.turns.append(({"role": "user", "content": deepcopy(content)}, reply, list(images)))
        self.step_indices.append(self.total_turns if step_index is None else step_index)
        self.total_turns += 1

    @property
    def history_image_count(self):
        return sum(len(pictures) for _, _, pictures in self.turns)

    def image_steps(self):
        return [*[None for _ in self.evidence_images],
                *[self.step_indices[index] for index, (_, _, pictures) in enumerate(self.turns)
                  for _ in pictures]]
