# Copyright 2021 The HuggingFace Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import shutil
import tempfile
import unittest

from transformers import PreTrainedTokenizer, PreTrainedTokenizerBase, PreTrainedTokenizerFast
from transformers.models.layoutlmv2 import LayoutLMv2Processor, LayoutLMv2Tokenizer, LayoutLMv2TokenizerFast
from transformers.models.layoutlmv2.tokenization_layoutlmv2 import VOCAB_FILES_NAMES
from transformers.testing_utils import require_pytesseract, require_tokenizers, require_torch, slow
from transformers.utils import FEATURE_EXTRACTOR_NAME, cached_property, is_pytesseract_available

from ...test_processing_common import ProcessorTesterMixin


if is_pytesseract_available():
    from transformers import LayoutLMv2ImageProcessor


@require_pytesseract
@require_tokenizers
class LayoutLMv2ProcessorTest(ProcessorTesterMixin, unittest.TestCase):
    tokenizer_class = LayoutLMv2Tokenizer
    rust_tokenizer_class = LayoutLMv2TokenizerFast
    processor_class = LayoutLMv2Processor

    def setUp(self):
        vocab_tokens = [
            "[UNK]",
            "[CLS]",
            "[SEP]",
            "[PAD]",
            "[MASK]",
            "want",
            "##want",
            "##ed",
            "wa",
            "un",
            "runn",
            "##ing",
            ",",
            "low",
            "lowest",
        ]

        image_processor_map = {
            "do_resize": True,
            "size": 224,
            "apply_ocr": True,
        }

        self.tmpdirname = tempfile.mkdtemp()
        self.vocab_file = os.path.join(self.tmpdirname, VOCAB_FILES_NAMES["vocab_file"])
        with open(self.vocab_file, "w", encoding="utf-8") as vocab_writer:
            vocab_writer.write("".join([x + "\n" for x in vocab_tokens]))
        self.image_processing_file = os.path.join(self.tmpdirname, FEATURE_EXTRACTOR_NAME)
        with open(self.image_processing_file, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(image_processor_map) + "\n")

    def get_tokenizer(self, **kwargs) -> PreTrainedTokenizer:
        return self.tokenizer_class.from_pretrained(self.tmpdirname, **kwargs)

    def get_rust_tokenizer(self, **kwargs) -> PreTrainedTokenizerFast:
        return self.rust_tokenizer_class.from_pretrained(self.tmpdirname, **kwargs)

    def get_tokenizers(self, **kwargs) -> list[PreTrainedTokenizerBase]:
        return [self.get_tokenizer(**kwargs), self.get_rust_tokenizer(**kwargs)]

    def get_image_processor(self, **kwargs):
        return LayoutLMv2ImageProcessor.from_pretrained(self.tmpdirname, **kwargs)

    def tearDown(self):
        shutil.rmtree(self.tmpdirname)

    def test_save_load_pretrained_default(self):
        image_processor = self.get_image_processor()
        tokenizers = self.get_tokenizers()
        for tokenizer in tokenizers:
            processor = LayoutLMv2Processor(image_processor=image_processor, tokenizer=tokenizer)

            processor.save_pretrained(self.tmpdirname)
            processor = LayoutLMv2Processor.from_pretrained(self.tmpdirname)

            self.assertEqual(processor.tokenizer.get_vocab(), tokenizer.get_vocab())
            self.assertIsInstance(processor.tokenizer, (LayoutLMv2Tokenizer, LayoutLMv2TokenizerFast))

            self.assertEqual(processor.image_processor.to_json_string(), image_processor.to_json_string())
            self.assertIsInstance(processor.image_processor, LayoutLMv2ImageProcessor)

    def test_save_load_pretrained_additional_features(self):
        processor = LayoutLMv2Processor(image_processor=self.get_image_processor(), tokenizer=self.get_tokenizer())
        processor.save_pretrained(self.tmpdirname)

        # slow tokenizer
        tokenizer_add_kwargs = self.get_tokenizer(bos_token="(BOS)", eos_token="(EOS)")
        image_processor_add_kwargs = self.get_image_processor(do_resize=False, size=30)

        processor = LayoutLMv2Processor.from_pretrained(
            self.tmpdirname, use_fast=False, bos_token="(BOS)", eos_token="(EOS)", do_resize=False, size=30
        )

        self.assertEqual(processor.tokenizer.get_vocab(), tokenizer_add_kwargs.get_vocab())
        self.assertIsInstance(processor.tokenizer, LayoutLMv2Tokenizer)

        self.assertEqual(processor.image_processor.to_json_string(), image_processor_add_kwargs.to_json_string())
        self.assertIsInstance(processor.image_processor, LayoutLMv2ImageProcessor)

        # fast tokenizer
        tokenizer_add_kwargs = self.get_rust_tokenizer(bos_token="(BOS)", eos_token="(EOS)")
        image_processor_add_kwargs = self.get_image_processor(do_resize=False, size=30)

        processor = LayoutLMv2Processor.from_pretrained(
            self.tmpdirname, bos_token="(BOS)", eos_token="(EOS)", do_resize=False, size=30
        )

        self.assertEqual(processor.tokenizer.get_vocab(), tokenizer_add_kwargs.get_vocab())
        self.assertIsInstance(processor.tokenizer, LayoutLMv2TokenizerFast)

        self.assertEqual(processor.image_processor.to_json_string(), image_processor_add_kwargs.to_json_string())
        self.assertIsInstance(processor.image_processor, LayoutLMv2ImageProcessor)

    def test_model_input_names(self):
        image_processor = self.get_image_processor()
        tokenizer = self.get_tokenizer()

        processor = LayoutLMv2Processor(tokenizer=tokenizer, image_processor=image_processor)

        input_str = "lower newer"
        image_input = self.prepare_image_inputs()

        # add extra args
        inputs = processor(text=input_str, images=image_input, return_codebook_pixels=False, return_image_mask=False)

        self.assertListEqual(list(inputs.keys()), processor.model_input_names)

    @slow
    def test_overflowing_tokens(self):
        # In the case of overflowing tokens, test that we still have 1-to-1 mapping between the images and input_ids (sequences that are too long are broken down into multiple sequences).

        from datasets import load_dataset

        # set up
        datasets = load_dataset("nielsr/funsd")
        processor = LayoutLMv2Processor.from_pretrained("microsoft/layoutlmv2-base-uncased", revision="no_ocr")

        def preprocess_data(examples):
            images = [image.convert("RGB") for image in examples["image"]]
            words = examples["words"]
            boxes = examples["bboxes"]
            word_labels = examples["ner_tags"]
            encoded_inputs = processor(
                images,
                words,
                boxes=boxes,
                word_labels=word_labels,
                padding="max_length",
                truncation=True,
                return_overflowing_tokens=True,
                stride=50,
                return_offsets_mapping=True,
                return_tensors="pt",
            )
            return encoded_inputs

        train_data = preprocess_data(datasets["train"])

        self.assertEqual(len(train_data["image"]), len(train_data["input_ids"]))


# different use cases tests
@require_torch
@require_pytesseract
class LayoutLMv2ProcessorIntegrationTests(unittest.TestCase):
    @cached_property
    def get_images(self):
        # we verify our implementation on 2 document images from the DocVQA dataset
        from datasets import load_dataset

        ds = load_dataset("hf-internal-testing/fixtures_docvqa", split="test")
        return ds[0]["image"].convert("RGB"), ds[1]["image"].convert("RGB")

    @cached_property
    def get_tokenizers(self):
        slow_tokenizer = LayoutLMv2Tokenizer.from_pretrained("microsoft/layoutlmv2-base-uncased")
        fast_tokenizer = LayoutLMv2TokenizerFast.from_pretrained("microsoft/layoutlmv2-base-uncased")
        return [slow_tokenizer, fast_tokenizer]

    @slow
    def test_processor_case_1(self):
        # case 1: document image classification (training, inference) + token classification (inference), apply_ocr = True

        image_processor = LayoutLMv2ImageProcessor()
        tokenizers = self.get_tokenizers
        images = self.get_images

        for tokenizer in tokenizers:
            processor = LayoutLMv2Processor(image_processor=image_processor, tokenizer=tokenizer)

            # not batched
            input_image_proc = image_processor(images[0], return_tensors="pt")
            input_processor = processor(images[0], return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify image
            self.assertAlmostEqual(input_image_proc["pixel_values"].sum(), input_processor["image"].sum(), delta=1e-2)

            # verify input_ids
            # this was obtained with Tesseract 4.1.1
            expected_decoding = "[CLS] 11 : 14 to 11 : 39 a. m 11 : 39 to 11 : 44 a. m. 11 : 44 a. m. to 12 : 25 p. m. 12 : 25 to 12 : 58 p. m. 12 : 58 to 4 : 00 p. m. 2 : 00 to 5 : 00 p. m. coffee break coffee will be served for men and women in the lobby adjacent to exhibit area. please move into exhibit area. ( exhibits open ) trrf general session ( part | ) presiding : lee a. waller trrf vice president “ introductory remarks ” lee a. waller, trrf vice presi - dent individual interviews with trrf public board members and sci - entific advisory council mem - bers conducted by trrf treasurer philip g. kuehn to get answers which the public refrigerated warehousing industry is looking for. plus questions from the floor. dr. emil m. mrak, university of cal - ifornia, chairman, trrf board ; sam r. cecil, university of georgia college of agriculture ; dr. stanley charm, tufts university school of medicine ; dr. robert h. cotton, itt continental baking company ; dr. owen fennema, university of wis - consin ; dr. robert e. hardenburg, usda. questions and answers exhibits open capt. jack stoney room trrf scientific advisory council meeting ballroom foyer [SEP]"  # fmt: skip
            decoding = processor.decode(input_processor.input_ids.squeeze().tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # batched
            input_image_proc = image_processor(images, return_tensors="pt")
            input_processor = processor(images, padding=True, return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify images
            self.assertAlmostEqual(input_image_proc["pixel_values"].sum(), input_processor["image"].sum(), delta=1e-2)

            # verify input_ids
            # this was obtained with Tesseract 4.1.1
            expected_decoding = "[CLS] 7 itc limited report and accounts 2013 itc ’ s brands : an asset for the nation the consumer needs and aspirations they fulfil, the benefit they generate for millions across itc ’ s value chains, the future - ready capabilities that support them, and the value that they create for the country, have made itc ’ s brands national assets, adding to india ’ s competitiveness. it is itc ’ s aspiration to be the no 1 fmcg player in the country, driven by its new fmcg businesses. a recent nielsen report has highlighted that itc's new fmcg businesses are the fastest growing among the top consumer goods companies operating in india. itc takes justifiable pride that, along with generating economic value, these celebrated indian brands also drive the creation of larger societal capital through the virtuous cycle of sustainable and inclusive growth. di wills * ; love delightfully soft skin? aia ans source : https : / / www. industrydocuments. ucsf. edu / docs / snbx0223 [SEP] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD] [PAD]"  # fmt: skip
            decoding = processor.decode(input_processor.input_ids[1].tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

    @slow
    def test_processor_case_2(self):
        # case 2: document image classification (training, inference) + token classification (inference), apply_ocr=False

        image_processor = LayoutLMv2ImageProcessor(apply_ocr=False)
        tokenizers = self.get_tokenizers
        images = self.get_images

        for tokenizer in tokenizers:
            processor = LayoutLMv2Processor(image_processor=image_processor, tokenizer=tokenizer)

            # not batched
            words = ["hello", "world"]
            boxes = [[1, 2, 3, 4], [5, 6, 7, 8]]
            input_processor = processor(images[0], words, boxes=boxes, return_tensors="pt")

            # verify keys
            expected_keys = ["input_ids", "bbox", "token_type_ids", "attention_mask", "image"]
            actual_keys = list(input_processor.keys())
            for key in expected_keys:
                self.assertIn(key, actual_keys)

            # verify input_ids
            expected_decoding = "[CLS] hello world [SEP]"
            decoding = processor.decode(input_processor.input_ids.squeeze().tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # batched
            words = [["hello", "world"], ["my", "name", "is", "niels"]]
            boxes = [[[1, 2, 3, 4], [5, 6, 7, 8]], [[3, 2, 5, 1], [6, 7, 4, 2], [3, 9, 2, 4], [1, 1, 2, 3]]]
            input_processor = processor(images, words, boxes=boxes, padding=True, return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            expected_decoding = "[CLS] hello world [SEP] [PAD] [PAD] [PAD]"
            decoding = processor.decode(input_processor.input_ids[0].tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # verify bbox
            expected_bbox = [
                [0, 0, 0, 0],
                [3, 2, 5, 1],
                [6, 7, 4, 2],
                [3, 9, 2, 4],
                [1, 1, 2, 3],
                [1, 1, 2, 3],
                [1000, 1000, 1000, 1000],
            ]
            self.assertListEqual(input_processor.bbox[1].tolist(), expected_bbox)

    @slow
    def test_processor_case_3(self):
        # case 3: token classification (training), apply_ocr=False

        image_processor = LayoutLMv2ImageProcessor(apply_ocr=False)
        tokenizers = self.get_tokenizers
        images = self.get_images

        for tokenizer in tokenizers:
            processor = LayoutLMv2Processor(image_processor=image_processor, tokenizer=tokenizer)

            # not batched
            words = ["weirdly", "world"]
            boxes = [[1, 2, 3, 4], [5, 6, 7, 8]]
            word_labels = [1, 2]
            input_processor = processor(images[0], words, boxes=boxes, word_labels=word_labels, return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "labels", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            expected_decoding = "[CLS] weirdly world [SEP]"
            decoding = processor.decode(input_processor.input_ids.squeeze().tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # verify labels
            expected_labels = [-100, 1, -100, 2, -100]
            self.assertListEqual(input_processor.labels.squeeze().tolist(), expected_labels)

            # batched
            words = [["hello", "world"], ["my", "name", "is", "niels"]]
            boxes = [[[1, 2, 3, 4], [5, 6, 7, 8]], [[3, 2, 5, 1], [6, 7, 4, 2], [3, 9, 2, 4], [1, 1, 2, 3]]]
            word_labels = [[1, 2], [6, 3, 10, 2]]
            input_processor = processor(
                images, words, boxes=boxes, word_labels=word_labels, padding=True, return_tensors="pt"
            )

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "labels", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            expected_decoding = "[CLS] my name is niels [SEP]"
            decoding = processor.decode(input_processor.input_ids[1].tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # verify bbox
            expected_bbox = [
                [0, 0, 0, 0],
                [3, 2, 5, 1],
                [6, 7, 4, 2],
                [3, 9, 2, 4],
                [1, 1, 2, 3],
                [1, 1, 2, 3],
                [1000, 1000, 1000, 1000],
            ]
            self.assertListEqual(input_processor.bbox[1].tolist(), expected_bbox)

            # verify labels
            expected_labels = [-100, 6, 3, 10, 2, -100, -100]
            self.assertListEqual(input_processor.labels[1].tolist(), expected_labels)

    @slow
    def test_processor_case_4(self):
        # case 4: visual question answering (inference), apply_ocr=True

        image_processor = LayoutLMv2ImageProcessor()
        tokenizers = self.get_tokenizers
        images = self.get_images

        for tokenizer in tokenizers:
            processor = LayoutLMv2Processor(image_processor=image_processor, tokenizer=tokenizer)

            # not batched
            question = "What's his name?"
            input_processor = processor(images[0], question, return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            # this was obtained with Tesseract 4.1.1
            expected_decoding = "[CLS] what's his name? [SEP] 11 : 14 to 11 : 39 a. m 11 : 39 to 11 : 44 a. m. 11 : 44 a. m. to 12 : 25 p. m. 12 : 25 to 12 : 58 p. m. 12 : 58 to 4 : 00 p. m. 2 : 00 to 5 : 00 p. m. coffee break coffee will be served for men and women in the lobby adjacent to exhibit area. please move into exhibit area. ( exhibits open ) trrf general session ( part | ) presiding : lee a. waller trrf vice president “ introductory remarks ” lee a. waller, trrf vice presi - dent individual interviews with trrf public board members and sci - entific advisory council mem - bers conducted by trrf treasurer philip g. kuehn to get answers which the public refrigerated warehousing industry is looking for. plus questions from the floor. dr. emil m. mrak, university of cal - ifornia, chairman, trrf board ; sam r. cecil, university of georgia college of agriculture ; dr. stanley charm, tufts university school of medicine ; dr. robert h. cotton, itt continental baking company ; dr. owen fennema, university of wis - consin ; dr. robert e. hardenburg, usda. questions and answers exhibits open capt. jack stoney room trrf scientific advisory council meeting ballroom foyer [SEP]"  # fmt: skip
            decoding = processor.decode(input_processor.input_ids.squeeze().tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # batched
            questions = ["How old is he?", "what's the time"]
            input_processor = processor(
                images, questions, padding="max_length", max_length=20, truncation=True, return_tensors="pt"
            )

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            # this was obtained with Tesseract 4.1.1
            expected_decoding = "[CLS] what's the time [SEP] 7 itc limited report and accounts 2013 itc ’ s [SEP]"
            decoding = processor.decode(input_processor.input_ids[1].tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # verify bbox
            expected_bbox = [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [1000, 1000, 1000, 1000], [0, 45, 67, 80], [72, 56, 109, 67], [72, 56, 109, 67], [116, 56, 189, 67], [198, 59, 253, 66], [257, 59, 285, 66], [289, 59, 365, 66], [372, 59, 407, 66], [74, 136, 161, 158], [74, 136, 161, 158], [74, 136, 161, 158], [74, 136, 161, 158], [1000, 1000, 1000, 1000]]  # fmt: skip
            self.assertListEqual(input_processor.bbox[1].tolist(), expected_bbox)

    @slow
    def test_processor_case_5(self):
        # case 5: visual question answering (inference), apply_ocr=False

        image_processor = LayoutLMv2ImageProcessor(apply_ocr=False)
        tokenizers = self.get_tokenizers
        images = self.get_images

        for tokenizer in tokenizers:
            processor = LayoutLMv2Processor(image_processor=image_processor, tokenizer=tokenizer)

            # not batched
            question = "What's his name?"
            words = ["hello", "world"]
            boxes = [[1, 2, 3, 4], [5, 6, 7, 8]]
            input_processor = processor(images[0], question, words, boxes, return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            expected_decoding = "[CLS] what's his name? [SEP] hello world [SEP]"
            decoding = processor.decode(input_processor.input_ids.squeeze().tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # batched
            questions = ["How old is he?", "what's the time"]
            words = [["hello", "world"], ["my", "name", "is", "niels"]]
            boxes = [[[1, 2, 3, 4], [5, 6, 7, 8]], [[3, 2, 5, 1], [6, 7, 4, 2], [3, 9, 2, 4], [1, 1, 2, 3]]]
            input_processor = processor(images, questions, words, boxes, padding=True, return_tensors="pt")

            # verify keys
            expected_keys = ["attention_mask", "bbox", "image", "input_ids", "token_type_ids"]
            actual_keys = sorted(input_processor.keys())
            self.assertListEqual(actual_keys, expected_keys)

            # verify input_ids
            expected_decoding = "[CLS] how old is he? [SEP] hello world [SEP] [PAD] [PAD] [PAD]"
            decoding = processor.decode(input_processor.input_ids[0].tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            expected_decoding = "[CLS] what's the time [SEP] my name is niels [SEP]"
            decoding = processor.decode(input_processor.input_ids[1].tolist())
            self.assertSequenceEqual(decoding, expected_decoding)

            # verify bbox
            expected_bbox = [[6, 7, 4, 2], [3, 9, 2, 4], [1, 1, 2, 3], [1, 1, 2, 3], [1000, 1000, 1000, 1000]]
            self.assertListEqual(input_processor.bbox[1].tolist()[-5:], expected_bbox)
