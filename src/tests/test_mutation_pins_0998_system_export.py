"""Pin for the CSV streaming buffer lifecycle.

Source: mutation delta of the release-0.99.8 checkpoint, 1 mutant.
Groups: exhausted buffer (1).
The private helper is exercised because subsequent public CSV chunks overwrite
residual bytes, hiding the buffer's postcondition from generator consumers.
"""

import csv
import io

from core.services.system.export import _stream_row


def test_streamed_row_leaves_an_empty_rewound_buffer():
    """Streaming consumes and rewinds its buffer: x__stream_row__mutmut_6."""
    output = io.StringIO()
    writer = csv.writer(output)
    assert _stream_row(writer, output, ["Example", "value"]) == "Example,value\r\n"
    assert output.getvalue() == ""
    assert output.tell() == 0
