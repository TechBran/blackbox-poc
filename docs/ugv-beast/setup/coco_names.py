"""COCO 80-class names matching YOLOv8 output indices."""

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# Class name → index lookup
COCO_INDEX = {name: i for i, name in enumerate(COCO_NAMES)}

# Colors for visualization (BGR, one per class, deterministic)
import hashlib
COCO_COLORS = []
for name in COCO_NAMES:
    h = hashlib.md5(name.encode()).hexdigest()
    COCO_COLORS.append((int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
