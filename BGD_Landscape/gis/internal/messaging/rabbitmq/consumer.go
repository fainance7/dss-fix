package rabbitmq

import (
	"context"
	"fmt"
	"log/slog"

	amqp "github.com/rabbitmq/amqp091-go"
)

// Handler processes a single delivery. Returning nil acks the message; returning
// an error nacks it (without requeue, to avoid poison-message loops).
type Handler interface {
	Handle(ctx context.Context, d amqp.Delivery) error
}

// Consumer declares a durable queue bound to the exchange by routing key and
// dispatches deliveries to a Handler. Each Consumer uses its own channel.
type Consumer struct {
	conn       *Connection
	queue      string
	bindingKey string
	tag        string
	handler    Handler
	log        *slog.Logger
}

// NewConsumer returns a Consumer for the given queue and routing-key binding.
func NewConsumer(conn *Connection, queue, bindingKey, tag string, handler Handler, log *slog.Logger) *Consumer {
	return &Consumer{
		conn:       conn,
		queue:      queue,
		bindingKey: bindingKey,
		tag:        tag,
		handler:    handler,
		log:        log,
	}
}

// Run declares/binds the queue and consumes until ctx is cancelled or the
// delivery channel closes. It uses manual acknowledgement.
func (c *Consumer) Run(ctx context.Context) error {
	ch, err := c.conn.openChannel()
	if err != nil {
		return fmt.Errorf("open channel: %w", err)
	}
	defer ch.Close()

	if err := c.setup(ch); err != nil {
		return err
	}

	deliveries, err := ch.Consume(
		c.queue, c.tag,
		false, // auto-ack: we ack manually
		false, // exclusive
		false, // no-local
		false, // no-wait
		nil,
	)
	if err != nil {
		return fmt.Errorf("start consume: %w", err)
	}

	c.log.Info("consumer started", "queue", c.queue, "binding", c.bindingKey, "tag", c.tag)

	for {
		select {
		case <-ctx.Done():
			if err := ch.Cancel(c.tag, false); err != nil {
				c.log.Warn("cancel consumer", "error", err)
			}
			c.log.Info("consumer stopped", "tag", c.tag)
			return ctx.Err()

		case d, ok := <-deliveries:
			if !ok {
				return fmt.Errorf("delivery channel closed for queue %q", c.queue)
			}
			c.dispatch(ctx, d)
		}
	}
}

func (c *Consumer) setup(ch *amqp.Channel) error {
	if _, err := ch.QueueDeclare(
		c.queue,
		true,  // durable
		false, // auto-delete
		false, // exclusive
		false, // no-wait
		nil,
	); err != nil {
		return fmt.Errorf("declare queue %q: %w", c.queue, err)
	}
	if err := ch.QueueBind(c.queue, c.bindingKey, c.conn.Exchange(), false, nil); err != nil {
		return fmt.Errorf("bind queue %q: %w", c.queue, err)
	}
	return nil
}

func (c *Consumer) dispatch(ctx context.Context, d amqp.Delivery) {
	if err := c.handler.Handle(ctx, d); err != nil {
		c.log.Error("handle delivery", "routing_key", d.RoutingKey, "error", err)
		if nackErr := d.Nack(false, false); nackErr != nil {
			c.log.Error("nack delivery", "error", nackErr)
		}
		return
	}
	if ackErr := d.Ack(false); ackErr != nil {
		c.log.Error("ack delivery", "error", ackErr)
	}
}
