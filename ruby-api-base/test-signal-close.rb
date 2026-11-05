require 'json'
require 'open3'
require 'rbconfig'
require 'timeout'

# Exercise a real blocked pipe write and concurrent close in both signal and ordinary context.
WITNESS = <<~'RUBY'
  require 'json'
  require 'timeout'
  mode = ARGV.fetch(0)
  reader, writer = IO.pipe
  while writer.write_nonblock('x' * 4096, exception: false).is_a?(Integer); end
  writing_thread = Thread.current
  closer = Thread.new do
    Timeout.timeout(2) do
      sleep 0.001 until writing_thread.status == 'sleep' && Array(writing_thread.backtrace).any? { |line| line.include?('syswrite') }
    end
    writer.close
  end
  write_once = -> { begin; writer.syswrite('y'); rescue IOError; end }
  if mode == 'trap'
    Signal.trap('USR1') { write_once.call }
    Process.kill('USR1', Process.pid)
  else
    write_once.call
  end
  completed = !!closer.join(2)
  STDOUT.puts JSON.generate(mode: mode, closeCompleted: completed)
  STDOUT.flush
  exit! completed ? 0 : 1
RUBY

# Supervise only the test-owned child and retain its failure without extending close deadlines.
def verify_close(mode)
  Open3.popen3(RbConfig.ruby, '-e', WITNESS, mode) do |input, output, error, process|
    input.close
    readers = [Thread.new { output.read }, Thread.new { error.read }]
    begin
      status = Timeout.timeout(6) { process.value }
      result = JSON.parse(readers[0].value)
      raise "pipe close failed in #{mode}: #{result.inspect} #{readers[1].value}" unless status.success? && result == { 'mode' => mode, 'closeCompleted' => true }
      puts JSON.generate(result)
    ensure
      if process.alive?
        Process.kill('KILL', process.pid) rescue Errno::ESRCH
        process.join
      end
      readers.each(&:join)
    end
  end
end

%w[thread trap].each { |mode| verify_close(mode) }
