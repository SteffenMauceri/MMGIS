import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Tuple

try:
    # Prefer absolute import in this repo layout
    from LLM.llm import create_chat_model, model_name as CURRENT_MODEL_NAME  # type: ignore
except Exception:
    # Fallback for direct execution when PYTHONPATH isn't set to project root
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from LLM.llm import create_chat_model, model_name as CURRENT_MODEL_NAME  # type: ignore


def _count_tokens_approx(text: str) -> int:
    """Count tokens for a string.

    Heuristics:
    - If tiktoken is available, use it (best-effort; model mapping may not be exact).
    - Else, approximate by whitespace-delimited words.
    - As a final guard, fall back to roughly 4 chars/token.
    """
    text = text or ""
    # Try tiktoken first
    try:
        import tiktoken  # type: ignore

        enc_name = None
        # Best-effort mapping for common families; otherwise default to cl100k_base
        name = (CURRENT_MODEL_NAME or "").lower()
        if any(k in name for k in ["gpt-4", "gpt-3.5", "o3", "o4", "gpt-4o", "gpt-4.1", "gpt-4.2"]):
            enc_name = "cl100k_base"
        elif any(k in name for k in ["llama", "llama3", "mistral", "mixtral", "qwen", "phi"]):
            enc_name = "cl100k_base"
        else:
            enc_name = "cl100k_base"

        enc = tiktoken.get_encoding(enc_name)
        return len(enc.encode(text))
    except Exception:
        pass

    # Next best: words as tokens
    words = [w for w in text.strip().split() if w]
    if words:
        return len(words)

    # Final fallback: chars/4
    return max(1, int(round(len(text) / 4)))


def _generate_placeholder_tokens(num_tokens: int, word: str = "placeholder") -> str:
    """Create a placeholder text roughly equal to num_tokens tokens (by words)."""
    num_tokens = max(0, int(num_tokens))
    if num_tokens == 0:
        return ""
    return (" ").join([word] * num_tokens)


@dataclass
class BenchmarkResult:
    prompt: str
    completion: str
    elapsed_seconds: float
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tokens_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.total_tokens / self.elapsed_seconds


def run_single_benchmark(prompt: str) -> BenchmarkResult:
    # Use a much longer timeout for benchmarking (default 5 minutes)
    # Override with BENCH_TIMEOUT_SEC env var
    try:
        bench_timeout = float(os.getenv("BENCH_TIMEOUT_SEC", "300"))
    except Exception:
        bench_timeout = 300.0
    
    # Temporarily override the timeout env var for this benchmark
    original_timeout = os.environ.get("LLM_REQUEST_TIMEOUT")
    os.environ["LLM_REQUEST_TIMEOUT"] = str(bench_timeout)
    
    try:
        model = create_chat_model(temperature=0)
    finally:
        # Restore original timeout
        if original_timeout is not None:
            os.environ["LLM_REQUEST_TIMEOUT"] = original_timeout
        else:
            os.environ.pop("LLM_REQUEST_TIMEOUT", None)

    start = time.perf_counter()
    # LangChain ChatOpenAI supports invoke(str)
    ai_msg = model.invoke(prompt)
    elapsed = time.perf_counter() - start

    completion_text = getattr(ai_msg, "content", str(ai_msg))

    input_tokens = _count_tokens_approx(prompt)
    output_tokens = _count_tokens_approx(completion_text)

    return BenchmarkResult(
        prompt=prompt,
        completion=completion_text,
        elapsed_seconds=elapsed,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def scale_to_time_windows(result: BenchmarkResult, windows_sec: List[int]) -> List[Tuple[int, int, int]]:
    """Given a measured result, scale to target windows by total token throughput.

    Returns a list of tuples: (window_seconds, est_input_tokens, est_output_tokens)
    Keeping the input/output ratio the same as the measured call.
    """
    tps = result.tokens_per_second
    if tps <= 0:
        return [(w, 0, 0) for w in windows_sec]

    in_ratio = 0.0
    out_ratio = 0.0
    if result.total_tokens > 0:
        in_ratio = result.input_tokens / result.total_tokens
        out_ratio = result.output_tokens / result.total_tokens

    scaled: List[Tuple[int, int, int]] = []
    for w in windows_sec:
        total = int(round(tps * float(w)))
        est_in = int(round(total * in_ratio))
        est_out = max(0, total - est_in)
        scaled.append((w, est_in, est_out))
    return scaled


def main() -> None:
    # A concise prompt that elicits a moderately long response for timing.
    # You can override via env BENCH_PROMPT.
    default_prompt = (
        "You are a concise technical writer. In about 180-250 words, summarize the key factors that affect "
        "sea ice formation, seasonal variability, and long-term trends. Use short paragraphs."
    )
    prompt = os.getenv("BENCH_PROMPT", default_prompt)

    # Time windows to consider (seconds). Override with BENCH_WINDOWS="1,5,30,60".
    windows_env = os.getenv("BENCH_WINDOWS", "1,5,30,60")
    try:
        windows = [int(x.strip()) for x in windows_env.split(",") if x.strip()]
    except Exception:
        windows = [1, 5, 30, 60]

    # Show timeout setting
    try:
        bench_timeout = float(os.getenv("BENCH_TIMEOUT_SEC", "300"))
    except Exception:
        bench_timeout = 300.0

    # Prepare output capture
    output_lines = []
    
    def print_and_capture(msg: str = "") -> None:
        print(msg)
        output_lines.append(msg)

    # Generate timestamp and output file path
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Save in LLM folder (parent of tests folder)
    llm_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_file = os.path.join(llm_dir, f"benchmark_results_{timestamp}.txt")

    print_and_capture(f"Benchmark Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_and_capture("=" * 60)
    print_and_capture(f"Model: {CURRENT_MODEL_NAME}")
    print_and_capture(f"Benchmark timeout: {bench_timeout:.0f} seconds")
    print_and_capture(f"Timing a single generation...")
    print_and_capture()

    try:
        result = run_single_benchmark(prompt)
    except Exception as e:
        error_msg = f"Error running benchmark: {e}"
        print_and_capture(error_msg)
        # Save error output too
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write("\n".join(output_lines))
            print(f"\nError log saved to: {output_file}")
        except Exception:
            pass
        sys.exit(1)

    print_and_capture("--- Measured Call ---")
    print_and_capture(f"Elapsed: {result.elapsed_seconds:.2f} s")
    print_and_capture(f"Input tokens (approx): {result.input_tokens}")
    print_and_capture(f"Output tokens (approx): {result.output_tokens}")
    print_and_capture(f"Total tokens (approx): {result.total_tokens}")
    if result.elapsed_seconds > 0:
        print_and_capture(f"Throughput (tokens/sec): {result.tokens_per_second:.2f}")
    print_and_capture()

    scaled = scale_to_time_windows(result, windows)

    print_and_capture("--- Estimated capacities by time window ---")
    for w, est_in, est_out in scaled:
        prompt_placeholder = _generate_placeholder_tokens(est_in, word="PROMPT")
        output_placeholder = _generate_placeholder_tokens(est_out, word="OUTPUT")
        print_and_capture(f"\n[{w} sec]")
        print_and_capture(f"Estimated tokens total: {est_in + est_out} (in: {est_in}, out: {est_out})")
        print_and_capture("Prompt placeholder:")
        print_and_capture(prompt_placeholder)
        print_and_capture("\nOutput placeholder:")
        print_and_capture(output_placeholder)

    # Add the actual prompt and response to the output
    print_and_capture("\n" + "=" * 60)
    print_and_capture("--- Actual Benchmark Call ---")
    print_and_capture("Prompt:")
    print_and_capture(result.prompt)
    print_and_capture("\nCompletion:")
    print_and_capture(result.completion)

    # Save to file
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))
        print(f"\nBenchmark results saved to: {output_file}")
    except Exception as e:
        print(f"\nWarning: Could not save results to file: {e}")


if __name__ == "__main__":
    main()


